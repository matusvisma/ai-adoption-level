#!/usr/bin/env python3
"""Summarise how Claude Code has actually been used on this machine.

Read-only. Nothing is written, sent or modified; the summary is printed as
JSON on stdout. Only the standard library is used.

Usage:
    python collect_evidence.py [--days 30] [--config-dir PATH ...] [--repo PATH ...]
                               [--author NAME_OR_EMAIL ...] [--no-samples]

People with more than one Claude Code seat often keep one config folder per
seat. Without --config-dir, the default folder and every sibling that looks
like one (~/.claude-work and so on) are read. Pass --config-dir once per
folder to name them yourself, for example for a folder somewhere else.

Without --repo, every repository that had a session in the window is
inspected, so there is normally no need to list them.

Sources:
    * session transcripts under <config>/projects/<project>/<session>.jsonl,
      for every config folder found or given
    * Claude Code configuration (user level and per repo)
    * subagent transcripts under <config>/projects/<project>/<session>/subagents/
    * git history, worktrees and CI files of each repository

The transcript format is not a stable interface. Every field is read
defensively; if the numbers look wrong (for example zero sessions although
transcripts exist), open one transcript and compare it with parse_session().
"""
import argparse
import bisect
import getpass
import json
import os
import platform
import re
import statistics
import subprocess
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
SUBAGENT_TOOLS = {"Task", "Agent"}
TEST_RE = re.compile(
    r"\b(pytest|dotnet test|npm (run )?test|pnpm (run )?test|yarn test|jest|vitest|"
    r"playwright test|go test|cargo test|mvn\b.*\btest|gradlew?\b.*\btest|phpunit|rspec)\b"
)
AI_TRAILER_RE = re.compile(
    r"co-authored-by:.*\b(claude|copilot|cursor|codex|devin|aider)\b|generated with.*claude",
    re.IGNORECASE,
)
# `git commit`, also with options before the subcommand (`git -C <path> commit`), not `git commit-tree`.
GIT_COMMIT_RE = re.compile(
    r"\bgit(?:\s+(?:-[cC]\s+(?:\"[^\"]*\"|'[^']*'|\S+)|--[\w-]+(?:=\S+)?))*\s+commit(?![\w-])")
PR_COMMAND_RE = re.compile(r"\b(gh pr create|az repos pr create|glab mr create)\b")
# MCP tools that open a pull or merge request, such as mcp__github__create_pull_request.
MCP_PR_TOOL_RE = re.compile(
    r"create[_-]?(pull[_-]?request|merge[_-]?request|pr)$|(pull[_-]?request|merge[_-]?request|pr)[_-]?create$",
    re.IGNORECASE,
)
# Worktrees that tools create inside the repository. Once a worktree is removed, git can no
# longer say which repository it belonged to, but its path still does.
WORKTREE_PATH_RE = re.compile(r"[\\/](?:\.claude[\\/]worktrees|\.worktrees)[\\/]")
NOISE_PREFIXES = ("<local-command", "<system-reminder", "<bash-", "Caveat:", "<task-notification",
                  # Compaction summary, for transcripts written before it carried isCompactSummary.
                  "This session is being continued from a previous conversation")
BIN_SECONDS = 600  # two sessions count as parallel when both act in the same 10 minutes


def parse_ts(value):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return None


def human_text(entry):
    """Text of a message the person typed, or None for tool results and noise."""
    if entry.get("type") != "user" or entry.get("isSidechain") or entry.get("isMeta"):
        return None
    # Compaction summaries are written as user messages, but nobody typed them.
    if entry.get("isCompactSummary") or entry.get("isVisibleInTranscriptOnly"):
        return None
    content = (entry.get("message") or {}).get("content")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        blocks = [b for b in content if isinstance(b, dict)]
        if any(b.get("type") == "tool_result" for b in blocks):
            return None
        text = "\n".join(b.get("text", "") for b in blocks if b.get("type") == "text")
    else:
        return None
    return text.strip() or None


def parse_session(path):
    s = {
        "file": path.name, "project_dir": path.parent.name, "cwd": None, "branches": set(),
        "start": None, "end": None, "prompts": 0, "interrupts": 0, "slash_commands": Counter(),
        "tools": Counter(), "files_edited": set(), "subagent_calls": 0, "skills": Counter(),
        "tests_run": 0, "commits": 0, "prs_created": 0, "worktree_cmds": 0, "plan_mode": 0,
        "longest_run": 0, "first_prompt": None, "bins": set(), "sidechain_tool_calls": 0,
        "opened_with_command": None, "per_turn_subagents": Counter(),
    }
    # Tool calls between human messages, keyed by how many human messages came before them.
    runs, prompt_times = Counter(), []
    try:
        handle = path.open(encoding="utf-8", errors="replace")
    except OSError:
        return None
    with handle:
        for line in handle:
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if not isinstance(e, dict):
                continue
            ts = parse_ts(e.get("timestamp"))
            if ts:
                s["start"] = ts if s["start"] is None else min(s["start"], ts)
                s["end"] = ts if s["end"] is None else max(s["end"], ts)
            if e.get("cwd") and not s["cwd"]:
                s["cwd"] = e["cwd"]
            if e.get("gitBranch"):
                s["branches"].add(e["gitBranch"])

            text = human_text(e)
            if text:
                if "[Request interrupted by user" in text:
                    s["interrupts"] += 1
                elif "<command-name>" in text:
                    m = re.search(r"<command-name>\s*(/?[\w:.-]+)", text)
                    s["slash_commands"][m.group(1) if m else "?"] += 1
                    if s["first_prompt"] is None and not s["opened_with_command"]:
                        s["opened_with_command"] = m.group(1) if m else "?"
                elif not text.startswith(NOISE_PREFIXES):
                    s["prompts"] += 1
                    if ts:
                        prompt_times.append(ts)
                    if s["first_prompt"] is None:
                        s["first_prompt"] = text
                continue

            if e.get("type") != "assistant":
                continue
            message = e.get("message") or {}
            content = message.get("content")
            # Parallel tool calls of one assistant turn share a message id.
            turn = message.get("id") or e.get("requestId") or e.get("uuid")
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict) or b.get("type") != "tool_use":
                    continue
                # Agent work without a human message in between, subagents included.
                runs[s["prompts"]] += 1
                if ts:
                    s["bins"].add(int(ts // BIN_SECONDS))
                if e.get("isSidechain"):
                    s["sidechain_tool_calls"] += 1
                    continue
                name, inp = str(b.get("name", "?")), b.get("input") or {}
                s["tools"]["mcp__*" if name.startswith("mcp__") else name] += 1
                if name in EDIT_TOOLS:
                    s["files_edited"].add(inp.get("file_path") or inp.get("notebook_path") or "?")
                elif name in SUBAGENT_TOOLS:
                    s["subagent_calls"] += 1
                    s["per_turn_subagents"][turn] += 1
                elif name == "Skill":
                    s["skills"][str(inp.get("skill") or inp.get("command") or "?")] += 1
                elif name == "ExitPlanMode":
                    s["plan_mode"] += 1
                elif name == "Bash":
                    cmd = str(inp.get("command", ""))
                    s["tests_run"] += bool(TEST_RE.search(cmd))
                    s["commits"] += bool(GIT_COMMIT_RE.search(cmd))
                    s["prs_created"] += bool(PR_COMMAND_RE.search(cmd))
                    s["worktree_cmds"] += "git worktree" in cmd
                elif name.startswith("mcp__"):
                    s["prs_created"] += bool(MCP_PR_TOOL_RE.search(name))
    subagents = scan_subagents(path.parent / path.stem / "subagents")
    prompt_times.sort()
    for t in subagents.pop("tool_times"):
        runs[bisect.bisect_right(prompt_times, t)] += 1
        s["bins"].add(int(t // BIN_SECONDS))
    s["longest_run"] = max(runs.values(), default=0)
    s.update(subagents)
    return s if s["start"] else None


def scan_subagents(directory):
    """When each subagent of a session ran, when it called tools, and which files it edited.

    Work done by subagents is not in the main transcript, so a session that
    delegates everything would otherwise look like it edited nothing.
    """
    spans, files, tool_times = [], set(), []
    for path in directory.glob("*.jsonl") if directory.is_dir() else []:
        first = last = None
        try:
            handle = path.open(encoding="utf-8", errors="replace")
        except OSError:
            continue
        with handle:
            for line in handle:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                ts = parse_ts(e.get("timestamp"))
                if ts:
                    first, last = (ts if first is None else min(first, ts)), (ts if last is None else max(last, ts))
                content = (e.get("message") or {}).get("content")
                for b in content if isinstance(content, list) else []:
                    if not isinstance(b, dict) or b.get("type") != "tool_use":
                        continue
                    if ts:
                        tool_times.append(ts)
                    if b.get("name") in EDIT_TOOLS:
                        inp = b.get("input") or {}
                        files.add(inp.get("file_path") or inp.get("notebook_path") or "?")
        if first is not None:
            spans.append((first, last))
    # Largest number of subagents whose activity overlapped in time.
    events = sorted([(a, 1) for a, _ in spans] + [(b + 0.001, -1) for _, b in spans])
    peak = live = 0
    for _, step in events:
        live += step
        peak = max(peak, live)
    return {"subagent_transcripts": len(spans), "max_concurrent_subagents": peak, "subagent_files_edited": files,
            "tool_times": tool_times}


_ROOTS = {}


def repo_root(cwd):
    """Main repository of a working directory, so worktrees group with their repo."""
    if cwd not in _ROOTS:
        root, resolved = cwd, False
        if cwd and Path(cwd).is_dir():
            common = git(cwd, "rev-parse", "--git-common-dir").strip()
            if common:
                p = Path(common) if Path(common).is_absolute() else Path(cwd) / common
                p = p.resolve()
                root = str(p.parent if p.name == ".git" else p)
                resolved = True
        if cwd and not resolved:
            # Git could not tell (the worktree may be gone): fall back to the path.
            head = WORKTREE_PATH_RE.split(cwd, maxsplit=1)
            if len(head) == 2:
                root = repo_root(head[0])
        _ROOTS[cwd] = root
    return _ROOTS[cwd]


def median(values):
    values = [v for v in values if v is not None]
    return round(statistics.median(values), 1) if values else None


def intervention(rows):
    """How much the person stepped in, over sessions that produced work.

    Buckets are by number of human prompts. The share of tool calls shows where
    the bulk of the work happened, which a count of sessions alone can hide.
    """
    buckets = {"1_to_3_prompts": (0, 3), "4_to_10_prompts": (4, 10), "11_plus_prompts": (11, 10 ** 9)}
    total = sum(r["tool_calls"] for r in rows) or 1
    out = {}
    for label, (low, high) in buckets.items():
        hit = [r for r in rows if low <= r["human_prompts"] <= high]
        out[label] = {
            "sessions": len(hit),
            "share_of_tool_calls_pct": round(100 * sum(r["tool_calls"] for r in hit) / total),
        }
    return out


def profile(rows):
    """The same measures for any group of sessions: all of them, or one repository."""
    work = [r for r in rows if r["working"]]

    def count(pred, group=rows):
        return sum(1 for r in group if pred(r))

    return {
        "sessions": len(rows),
        "working_sessions": len(work),
        "sessions_without_changes": len(rows) - len(work),
        "files_per_working_session": {
            "one": count(lambda r: r["files_edited"] <= 1, work),
            "2_to_5": count(lambda r: 2 <= r["files_edited"] <= 5, work),
            "6_plus": count(lambda r: r["files_edited"] >= 6, work),
        },
        "intervention_in_working_sessions": intervention(work),
        "median_human_prompts_per_working_session": median(r["human_prompts"] for r in work),
        "median_first_prompt_chars_in_working_sessions": median(r["first_prompt_chars"] for r in work),
        "working_sessions_opened_with_a_command": count(lambda r: r["opened_with_command"], work),
        "interrupts": sum(r["interrupts"] for r in rows),
        "sessions_with_run_of_20_plus_tool_calls": count(lambda r: r["longest_run_without_human"] >= 20),
        "sessions_with_run_of_50_plus_tool_calls": count(lambda r: r["longest_run_without_human"] >= 50),
        "sessions_running_tests": count(lambda r: r["tests_run"]),
        "sessions_committing": count(lambda r: r["commits"]),
        "sessions_creating_pr": count(lambda r: r["prs_created"]),
        "sessions_using_plan_mode": count(lambda r: r["plan_mode"]),
        "sessions_using_subagents": count(lambda r: r["subagent_calls"]),
        "sessions_with_subagents_running_at_once": count(lambda r: r["max_concurrent_subagents"] >= 2),
    }


def summarise_sessions(sessions, samples):
    total_tools = Counter()
    per_bin = defaultdict(set)
    rows = []
    for i, s in enumerate(sessions):
        total_tools.update(s["tools"])
        for b in s["bins"]:
            per_bin[b].add(i)
        n_tools = sum(s["tools"].values())
        files = len(s["files_edited"] | s["subagent_files_edited"])
        rows.append({
            "date": datetime.fromtimestamp(s["start"], timezone.utc).strftime("%Y-%m-%d"),
            "config_dir": s["config_dir"],
            "repo": repo_root(s["cwd"]) if s["cwd"] else s["project_dir"],
            "cwd": s["cwd"] or s["project_dir"],
            "branches": sorted(s["branches"])[:3],
            "working": bool(files or s["commits"] or s["subagent_calls"]),
            "active_minutes": len(s["bins"]) * BIN_SECONDS // 60,
            "human_prompts": s["prompts"],
            "interrupts": s["interrupts"],
            "tool_calls": n_tools,
            "longest_run_without_human": s["longest_run"],
            "files_edited": files,
            "subagent_calls": s["subagent_calls"],
            "max_subagents_launched_in_one_turn": max(s["per_turn_subagents"].values(), default=0),
            "max_concurrent_subagents": s["max_concurrent_subagents"],
            "tests_run": s["tests_run"],
            "commits": s["commits"],
            "prs_created": s["prs_created"],
            "plan_mode": s["plan_mode"],
            "opened_with_command": s["opened_with_command"],
            "first_prompt_chars": len(s["first_prompt"] or ""),
            **({"first_prompt": (s["first_prompt"] or "")[:500]} if samples else {}),
        })

    # Sessions acting in the same 10 minutes. Related work shows up as one
    # repository worked on from different worktrees or branches at once.
    groups, same_repo, parallel_days = Counter(), 0, set()
    for b, members in per_bin.items():
        if len(members) < 2:
            continue
        parallel_days.add(datetime.fromtimestamp(b * BIN_SECONDS, timezone.utc).strftime("%Y-%m-%d"))
        groups[tuple(sorted({rows[i]["repo"] for i in members}))] += 1
        places = defaultdict(set)
        for i in members:
            places[rows[i]["repo"]].add((rows[i]["cwd"], tuple(rows[i]["branches"])))
        same_repo += any(len(v) >= 2 for v in places.values())

    by_repo, by_config = defaultdict(list), defaultdict(list)
    for r in rows:
        by_repo[r["repo"]].append(r)
        by_config[r["config_dir"]].append(r)
    activity = Counter({repo: len(group) for repo, group in by_repo.items()})
    top = sorted(rows, key=lambda r: r["tool_calls"], reverse=True)[:15]
    return {
        "active_days": len({r["date"] for r in rows}),
        "all_repos": profile(rows),
        "by_repo": {repo: profile(group) for repo, group in sorted(by_repo.items(), key=lambda kv: -len(kv[1]))},
        "by_config_dir": {c: {**profile(group), "repos": sorted({r["repo"] for r in group})}
                          for c, group in by_config.items()},
        "parallel_work": {
            "days_with_parallel_sessions": len(parallel_days),
            "ten_minute_windows_with_2_plus_sessions": sum(1 for m in per_bin.values() if len(m) >= 2),
            "ten_minute_windows_with_3_plus_sessions": sum(1 for m in per_bin.values() if len(m) >= 3),
            "windows_with_one_repo_in_2_plus_worktrees_or_branches": same_repo,
            "most_common_parallel_groups": [{"repos": list(g), "windows": n} for g, n in groups.most_common(5)],
            "sessions_with_worktree_commands": sum(1 for s in sessions if s["worktree_cmds"]),
            "total_subagent_calls": sum(r["subagent_calls"] for r in rows),
            "most_subagents_running_at_once": max((r["max_concurrent_subagents"] for r in rows), default=0),
            "most_subagents_launched_in_one_turn": max((r["max_subagents_launched_in_one_turn"] for r in rows), default=0),
        },
        "tool_use_counts": dict(total_tools.most_common(20)),
        "skills_invoked": dict(sum((s["skills"] for s in sessions), Counter()).most_common(15)),
        "slash_commands": dict(sum((s["slash_commands"] for s in sessions), Counter()).most_common(15)),
        "largest_sessions": top,
    }, sorted(activity, key=lambda repo: (-activity[repo], repo))  # busiest first, for the repo cap


def names(directory, pattern):
    return sorted(p.stem if p.is_file() else p.name for p in directory.glob(pattern)) if directory.is_dir() else []


def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def inventory(claude_dir):
    """Names only: what is configured, never file contents or secret values."""
    hooks, allow = set(), 0
    for name in ("settings.json", "settings.local.json"):
        data = load_json(claude_dir / name)
        hooks.update((data.get("hooks") or {}).keys())
        allow += len((data.get("permissions") or {}).get("allow") or [])
    return {
        "path": str(claude_dir),
        "exists": claude_dir.is_dir(),
        "agents": names(claude_dir / "agents", "*.md"),
        "skills": names(claude_dir / "skills", "*/"),
        "commands": names(claude_dir / "commands", "*.md"),
        "workflows": names(claude_dir / "workflows", "*.js"),
        "hook_events": sorted(hooks),
        "permission_allow_rules": allow,
    }


def git(repo, *args):
    try:
        # Decode as UTF-8 explicitly: the Windows default code page fails on commit messages.
        out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, timeout=30,
                             encoding="utf-8", errors="replace")
        return out.stdout if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def my_identities(root, extra=()):
    """Names and emails whose commits count as the person's own.

    The configured name catches commits made under another email (a no-reply
    address, a second machine); `extra` adds identities git config does not know.
    """
    found = list(extra)
    for key in ("user.name", "user.email"):
        found += [git(root, "config", key).strip(), git(root, "config", "--global", key).strip()]
    return sorted({f for f in found if f})


def repo_evidence(repo, days, authors=()):
    root = git(repo, "rev-parse", "--show-toplevel").strip()
    if not root:
        return {"path": str(repo), "git": False}
    root = Path(root)
    identities = my_identities(root, authors)
    # Several --author options match any of them; -F and -i match each as plain, case-blind text.
    log = git(root, "log", "--all", f"--since={days} days ago", "--fixed-strings", "--regexp-ignore-case",
              *[f"--author={i}" for i in identities], "--pretty=format:%B%x1e") if identities else ""
    messages = [m for m in log.split("\x1e") if m.strip()]
    by_tool = Counter()
    for m in messages:
        hit = AI_TRAILER_RE.search(m)
        if hit:
            by_tool[(hit.group(1) or "claude").lower()] += 1
    ci = []
    candidates = list((root / ".github" / "workflows").glob("*.y*ml")) if (root / ".github").is_dir() else []
    candidates += list(root.glob("azure-pipelines*.y*ml")) + list(root.glob(".gitlab-ci.y*ml"))
    for f in candidates:
        try:
            if re.search(r"claude|anthropic", f.read_text(encoding="utf-8", errors="replace"), re.I):
                ci.append(str(f.relative_to(root)))
        except OSError:
            pass
    return {
        "path": str(root),
        "git": True,
        "my_commits_in_window": len(messages),
        "my_commits_with_ai_trailer": sum(by_tool.values()),
        "ai_trailers_by_tool": dict(by_tool),
        "worktrees": max(git(root, "worktree", "list", "--porcelain").count("worktree "), 1),
        "ci_files_mentioning_claude": ci,
        "instruction_files": [n for n in ("CLAUDE.md", "AGENTS.md", ".claude/CLAUDE.md") if (root / n).is_file()],
        "mcp_servers": sorted((load_json(root / ".mcp.json").get("mcpServers") or {}).keys()),
        "claude_config": inventory(root / ".claude"),
    }


def employee():
    """Who ran this, so reports collected from many people can be told apart.

    The git identity is the best local guess at a full name; the OS account is
    the fallback. The skill confirms the name with the person before reporting.
    """
    try:
        account = getpass.getuser()
    except Exception:  # getuser raises various errors when no account name is set
        account = ""
    return {
        "git_user_name": git(Path.home(), "config", "--global", "user.name").strip()
                         or git(Path.cwd(), "config", "user.name").strip(),
        "os_account": account,
    }


def has_sessions(path):
    return (path / "projects").is_dir()


def discover_config_dirs():
    """Every Claude Code config folder on this machine that holds sessions.

    One seat usually means one folder, selected with CLAUDE_CONFIG_DIR. People
    name them freely, so look for anything shaped like one next to the default.
    """
    home = Path.home()
    found = [Path(os.environ["CLAUDE_CONFIG_DIR"]).expanduser()] if os.environ.get("CLAUDE_CONFIG_DIR") else []
    found.append(home / ".claude")
    patterns = [(home, ".claude*"), (home, ".config/claude*")]
    windows_users = Path("/mnt/c/Users")  # the Windows side, when running inside WSL
    if windows_users.is_dir():
        patterns.append((windows_users, "*/.claude*"))
    for base, pattern in patterns:
        try:
            found += sorted(p for p in base.glob(pattern) if p.is_dir())
        except OSError:
            pass
    unique = {}
    for path in found:
        try:
            if has_sessions(path):
                unique.setdefault(str(path.resolve()), path)
        except OSError:
            pass
    return list(unique.values())


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=30, help="look-back window (default 30)")
    ap.add_argument("--repo", action="append", default=[],
                    help="repository to inspect; repeatable (default: every repository with sessions)")
    ap.add_argument("--config-dir", action="append", default=[],
                    help="Claude Code config folder to read; repeatable (default: every folder found)")
    ap.add_argument("--author", action="append", default=[],
                    help="another name or email of yours in git history; repeatable "
                         "(default: the configured git user.name and user.email)")
    ap.add_argument("--no-samples", action="store_true", help="leave prompt text out of the output")
    args = ap.parse_args()

    configs = [Path(c).expanduser() for c in args.config_dir] or discover_config_dirs()
    cutoff = time.time() - args.days * 86400
    sessions, config_report, seen_files = [], [], set()
    for config in configs:
        files, unreadable = [], 0
        for p in (config / "projects").glob("*/*.jsonl") if has_sessions(config) else []:
            try:
                if p.stat().st_mtime >= cutoff:
                    files.append(p)
            except OSError:  # locked, or deleted since the listing: skip it, do not abort the run
                unreadable += 1
        kept = 0
        for path in files:
            if path.name in seen_files:  # the same session copied into two folders
                continue
            s = parse_session(path)
            # Sessions with no tool calls and no prompts are empty shells (opened and closed).
            if s and s["end"] >= cutoff and (s["prompts"] or s["tools"]):
                seen_files.add(path.name)
                s["config_dir"] = str(config)
                sessions.append(s)
                kept += 1
        config_report.append({
            "path": str(config), "found": has_sessions(config),
            "transcript_files_in_window": len(files), "sessions_in_window": kept,
            "unreadable_transcript_files": unreadable,
            "setup": inventory(config),
        })

    usage, seen = summarise_sessions(sessions, samples=not args.no_samples)
    repos = args.repo or [r for r in seen if Path(r).is_dir()][:20] or ["."]
    result = {
        "window_days": args.days,
        "generated_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
        "employee": employee(),
        "machine": platform.node(),
        "config_dirs": config_report,
        "note": "Transcripts are deleted after a retention period, so a long window may be incomplete. "
                "Only config folders listed in config_dirs were read; a seat kept in a folder elsewhere, "
                "in WSL or on another machine needs --config-dir or its own run.",
        "usage": usage,
        "repos": [repo_evidence(Path(r).expanduser(), args.days, args.author) for r in repos],
    }
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    json.dump(result, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
