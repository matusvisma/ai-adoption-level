"""Tests for collect_evidence.py, built on synthetic transcripts.

Run from the repository root:

    python -m unittest discover -s tests
"""
import contextlib
import importlib.util
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

SCRIPT = (Path(__file__).resolve().parent.parent / "plugins" / "ai-adoption" / "skills" / "level"
          / "scripts" / "collect_evidence.py")
_spec = importlib.util.spec_from_file_location("collect_evidence", SCRIPT)
ce = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ce)

# An hour ago, on a 10-minute boundary, so the number of activity bins is predictable.
T0 = (int(time.time()) // ce.BIN_SECONDS) * ce.BIN_SECONDS - 3600
SUMMARY = ("This session is being continued from a previous conversation that ran out of "
           "context. Summary: ...")
_ids = itertools.count()


def iso(minute):
    return datetime.fromtimestamp(T0 + minute * 60, timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def prompt(text, minute, cwd="/work/repo", branch="main", **extra):
    return {"type": "user", "timestamp": iso(minute), "cwd": cwd, "gitBranch": branch,
            "isSidechain": False, "message": {"role": "user", "content": text}, **extra}


def tool_use(name, minute, inp=None, cwd="/work/repo", branch="main", sidechain=False):
    n = next(_ids)
    return {"type": "assistant", "timestamp": iso(minute), "cwd": cwd, "gitBranch": branch,
            "isSidechain": sidechain,
            "message": {"id": f"msg_{n}", "role": "assistant",
                        "content": [{"type": "tool_use", "id": f"toolu_{n}", "name": name,
                                     "input": inp or {}}]}}


def write_jsonl(path, entries):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")


class TempDirTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.config = self.tmp / "config"

    def write_session(self, entries, project="proj", name=None):
        path = self.config / "projects" / project / f"{name or uuid.uuid4()}.jsonl"
        write_jsonl(path, entries)
        return path

    def parse(self, entries):
        return ce.parse_session(self.write_session(entries))

    def collect(self):
        out = subprocess.run(
            [sys.executable, str(SCRIPT), "--config-dir", str(self.config), "--repo", str(self.tmp),
             "--days", "30", "--no-samples"],
            capture_output=True, encoding="utf-8", check=True, cwd=self.tmp)
        return json.loads(out.stdout)


class CompactionSummaryTest(TempDirTest):
    """Bug 1: compaction summaries were counted as prompts the person typed."""

    def session_with_summary(self, **flags):
        return self.parse([
            prompt("Implement feature X", 0),
            tool_use("Edit", 1, {"file_path": "a.py"}),
            tool_use("Edit", 2, {"file_path": "b.py"}),
            prompt(SUMMARY, 3, **flags),
            tool_use("Edit", 4, {"file_path": "c.py"}),
            tool_use("Bash", 5, {"command": "pytest"}),
        ])

    def test_flagged_summary_is_not_a_prompt(self):
        s = self.session_with_summary(isCompactSummary=True, isVisibleInTranscriptOnly=True)
        self.assertEqual(s["prompts"], 1)

    def test_flagged_summary_does_not_split_the_run(self):
        s = self.session_with_summary(isCompactSummary=True, isVisibleInTranscriptOnly=True)
        self.assertEqual(s["longest_run"], 4)

    def test_unflagged_summary_from_older_versions_is_not_a_prompt(self):
        s = self.session_with_summary()
        self.assertEqual((s["prompts"], s["longest_run"]), (1, 4))

    def test_summary_is_never_taken_as_the_opening_prompt(self):
        s = self.parse([
            prompt(SUMMARY, 0, isCompactSummary=True, isVisibleInTranscriptOnly=True),
            tool_use("Edit", 1, {"file_path": "a.py"}),
            prompt("Now add tests", 2),
        ])
        self.assertEqual(s["first_prompt"], "Now add tests")


class WorktreeGroupingTest(TempDirTest):
    """Bug 2: a worktree that had been removed was reported as a repository of its own."""

    def test_removed_worktree_maps_to_its_repository(self):
        repo = (self.tmp / "gone" / "repo").as_posix()
        for sep in ("/", "\\"):
            base = repo.replace("/", sep)
            for tail in ([".claude", "worktrees", "feat-a"], [".claude", "worktrees", "feat-a", "src"],
                         [".worktrees", "feat-b"]):
                cwd = sep.join([base, *tail])
                with self.subTest(cwd=cwd):
                    self.assertEqual(ce.repo_root(cwd), base)

    @unittest.skipUnless(shutil.which("git"), "git is not installed")
    def test_existing_repository_under_a_worktree_folder_is_its_own_repository(self):
        clone = self.tmp / "parent" / ".worktrees" / "other"
        clone.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(clone)], check=True, capture_output=True)
        self.assertEqual(Path(ce.repo_root(str(clone))).resolve(), clone.resolve())

    def test_removed_worktree_and_main_checkout_at_once_are_parallel_work_in_one_repo(self):
        repo = (self.tmp / "gone" / "repo").as_posix()
        worktree = repo + "/.claude/worktrees/feat-a"
        self.write_session([prompt("A", 0, cwd=worktree, branch="feat-a"),
                            tool_use("Edit", 1, {"file_path": "a.py"}, cwd=worktree, branch="feat-a")])
        self.write_session([prompt("B", 0, cwd=repo),
                            tool_use("Edit", 2, {"file_path": "b.py"}, cwd=repo)])
        usage = self.collect()["usage"]
        self.assertEqual(list(usage["by_repo"]), [repo])
        self.assertEqual(
            usage["parallel_work"]["windows_with_one_repo_in_2_plus_worktrees_or_branches"], 1)


class SubagentActivityTest(TempDirTest):
    """Bug 3: work done by subagents was missing from activity time and run length."""

    def session_with_subagent(self, main_entries, subagent_entries):
        path = self.write_session(main_entries)
        write_jsonl(path.parent / path.stem / "subagents" / "agent-1.jsonl", subagent_entries)
        return ce.parse_session(path)

    def delegating_session(self):
        return self.session_with_subagent(
            [prompt("Delegate feature Z", 0), tool_use("Agent", 0, {"prompt": "do Z"})],
            [tool_use("Edit", m, {"file_path": f"z{m}.py"}, sidechain=True) for m in range(0, 40, 5)])

    def test_subagent_activity_counts_toward_active_time(self):
        self.assertEqual(len(self.delegating_session()["bins"]), 4)

    def test_subagent_tool_calls_count_toward_the_run_without_human(self):
        self.assertEqual(self.delegating_session()["longest_run"], 9)

    def test_a_prompt_during_subagent_work_splits_the_run(self):
        s = self.session_with_subagent(
            [prompt("Start in background", 0), tool_use("Agent", 0, {"prompt": "do Z"}),
             prompt("Meanwhile, check the build", 10), tool_use("Bash", 11, {"command": "make"})],
            [tool_use("Edit", m, {"file_path": f"z{m}.py"}, sidechain=True) for m in (1, 2, 12, 13, 14)])
        self.assertEqual(s["longest_run"], 4)

    def test_inline_sidechain_tool_calls_count_toward_the_run(self):
        s = self.parse([prompt("Delegate", 0), tool_use("Task", 0, {"prompt": "do Z"}),
                        *[tool_use("Edit", m, {"file_path": f"z{m}.py"}, sidechain=True) for m in (1, 2, 3)]])
        self.assertEqual(s["longest_run"], 4)


class CommitAndPrDetectionTest(TempDirTest):
    """Bug 4: commits through `git -C` and PRs opened through MCP or glab were missed."""

    def counts(self, name, inp=None):
        s = self.parse([prompt("Ship it", 0), tool_use(name, 1, inp)])
        return {"commits": s["commits"], "prs": s["prs_created"]}

    def bash(self, command):
        return self.counts("Bash", {"command": command})

    def test_plain_commit_is_counted(self):
        self.assertEqual(self.bash("git commit -m wip")["commits"], 1)

    def test_commit_with_dash_C_is_counted(self):
        self.assertEqual(self.bash("git -C D:/work/repo commit -m wip")["commits"], 1)

    def test_commit_with_quoted_path_and_config_option_is_counted(self):
        self.assertEqual(self.bash('git -c core.hooksPath=x -C "D:/my repo" commit -m wip')["commits"], 1)

    def test_commit_tree_is_not_a_commit(self):
        self.assertEqual(self.bash("git commit-tree 4b825dc -m x")["commits"], 0)

    def test_pr_opened_through_mcp_is_counted(self):
        for name in ("mcp__github__create_pull_request", "mcp__azure-devops__repo_create_pull_request",
                     "mcp__gitlab__create_merge_request"):
            with self.subTest(name=name):
                self.assertEqual(self.counts(name)["prs"], 1)

    def test_other_pull_request_tools_are_not_prs(self):
        for name in ("mcp__github__create_pull_request_review", "mcp__github__get_pull_request"):
            with self.subTest(name=name):
                self.assertEqual(self.counts(name)["prs"], 0)

    def test_gitlab_merge_request_from_the_shell_is_counted(self):
        self.assertEqual(self.bash("glab mr create --fill")["prs"], 1)

    def test_github_pr_from_the_shell_is_counted(self):
        self.assertEqual(self.bash("gh pr create --fill")["prs"], 1)


class RepoOrderTest(TempDirTest):
    """Bug 5: the 20-repository cap kept repositories by name, not by activity."""

    def test_repositories_are_listed_busiest_first(self):
        sessions = []
        for cwd, count in (("/work/a-quiet", 1), ("/work/z-busy", 3)):
            for i in range(count):
                s = self.parse([prompt("x", i, cwd=cwd), tool_use("Edit", i, {"file_path": "f.py"}, cwd=cwd)])
                s["config_dir"] = "cfg"
                sessions.append(s)
        _, repos = ce.summarise_sessions(sessions, samples=False)
        self.assertEqual(repos, ["/work/z-busy", "/work/a-quiet"])


@unittest.skipUnless(shutil.which("git"), "git is not installed")
class CommitAttributionTest(TempDirTest):
    """Bug 6: only commits under the repository's user.email were counted."""

    def setUp(self):
        super().setUp()
        empty = self.tmp / "empty.gitconfig"
        empty.write_text("", encoding="utf-8")
        patcher = mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.repo = self.tmp / "repo"
        self.repo.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Test Person")
        self.git("config", "user.email", "test.person@work.example")
        trailer = "\n\nCo-Authored-By: Claude <noreply@anthropic.com>"
        self.commit("Test Person <test.person@work.example>", "Add a" + trailer)
        self.commit("Test Person <12345+tperson@users.noreply.github.com>", "Add b" + trailer)
        self.commit("Someone Else <else@work.example>", "Add c")

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.repo), *args], check=True, capture_output=True)

    def commit(self, author, message):
        self.git("commit", "-q", "--allow-empty", "--author", author, "-m", message)

    def test_commits_under_another_email_with_the_same_name_are_counted(self):
        evidence = ce.repo_evidence(self.repo, 30)
        self.assertEqual((evidence["my_commits_in_window"], evidence["my_commits_with_ai_trailer"]), (2, 2))

    def test_extra_author_identities_are_counted(self):
        evidence = ce.repo_evidence(self.repo, 30, authors=["else@work.example"])
        self.assertEqual(evidence["my_commits_in_window"], 3)


class UnreadableTranscriptTest(TempDirTest):
    """Bug 7: one transcript that could not be stat()-ed aborted the whole run."""

    def test_unreadable_transcript_is_skipped(self):
        self.write_session([prompt("x", 0), tool_use("Edit", 1, {"file_path": "a.py"})], name="readable")
        self.write_session([prompt("y", 0), tool_use("Edit", 1, {"file_path": "b.py"})], name="locked")
        real_stat = Path.stat

        def stat(path, *args, **kwargs):
            if path.name == "locked.jsonl":
                raise PermissionError(13, "Permission denied", str(path))
            return real_stat(path, *args, **kwargs)

        argv = ["collect_evidence.py", "--config-dir", str(self.config), "--repo", str(self.tmp), "--no-samples"]
        out = io.StringIO()
        with mock.patch.object(Path, "stat", stat), mock.patch.object(sys, "argv", argv), \
                contextlib.redirect_stdout(out):
            ce.main()
        self.assertEqual(json.loads(out.getvalue())["config_dirs"][0]["sessions_in_window"], 1)


if __name__ == "__main__":
    unittest.main()
