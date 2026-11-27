---
name: level
description: Assess which AI adoption level an engineer actually works at (Level 1 Assistance, Level 2 Supervision, Level 3 Delegation, Level 4 Orchestration), based on evidence from their real Claude Code usage plus a short interview, and report what separates them from the next level. Use whenever the user asks what AI adoption or AI maturity level they are at, wants a self-assessment of how they use AI for engineering, asks things like "am I level 3?", "how am I using AI?", "how do I get to the next level?", or wants a baseline before a review or a team adoption check-in.
---

# AI adoption level check

Work out which of four AI adoption levels describes how this person really
does engineering work, show the evidence, and say what the next level would
take. The level definitions and their numbered criteria (L1.1 … L4.5) are in
`references/levels.md`. Read that file first; it is the source of truth and
may have been edited by the organisation.

## What is being measured

The levels describe a working mode: who holds the implementation and where
the engineer's attention goes. They are not a checklist that accumulates
(L1.2 "writes most of the implementation" is the opposite of L3.4), and they
are not a measure of which tools are installed.

| | Unit handed to AI | What the engineer does while it runs |
|---|---|---|
| Level 1 | A line, a method, a question | Types the code, accepts or rejects suggestions |
| Level 2 | A task or feature, in a live session | Steers turn by turn, reviews, corrects |
| Level 3 | A specified piece of work, through to PR | Writes the spec and acceptance criteria up front, then supervises |
| Level 4 | An initiative split across several agents | Decomposes, manages dependencies and integration, builds the gates |

So report two things:

- **Working level**: the mode that describes most of their engineering work
  in the window. This is the answer to "which level am I".
- **Reach**: the highest level they demonstrably worked at, even once or
  twice. This shows where they are heading.

The assessment is useful only if it is accurate. People tend to rate
themselves by their best day, and a flattering result gives them nothing to
act on. Equally, a higher level is not a moral score: plenty of work is
rightly done at Level 2. Report what the evidence shows, without praise and
without judgement.

## Step 1: Gather evidence

Evidence of behaviour outranks self-description. Start with what can be
observed and use the interview only for what cannot.

**With shell and file access (Claude Code, Cowork)**, run the bundled
collector. It sits in the `scripts/` folder next to this SKILL.md, so use that
full path. It is read-only and prints JSON:

```bash
python <this skill folder>/scripts/collect_evidence.py --days 30
```

It finds every repository that had a session in the window, so there is no
need to list them (`--repo` narrows it). The output has the same measures for
all sessions (`all_repos`), for each repository (`by_repo`) and for each
Claude Code config folder (`by_config_dir`), a `parallel_work` block, the
fifteen largest sessions, the Claude Code setup, and git history per
repository. Things to know about it:

- People with more than one Claude Code seat usually keep one config folder
  per seat. The collector reads the default folder and any sibling that looks
  like one (`~/.claude-work` and similar), and lists what it read in
  `config_dirs`. A folder elsewhere is read only when passed with
  `--config-dir <path>`; the flag can be repeated, and once it is used, name
  every folder that should count.
- Commits count as the person's own when the author matches their
  configured git `user.name` or `user.email`. If they also commit under
  another name or address, rerun with `--author <name or email>`
  (repeatable).

- Most numbers are about **working sessions**: sessions that changed files,
  committed, or handed work to subagents. Sessions that only answered a
  question are counted separately and say little about the level.
- `intervention_in_working_sessions` splits working sessions by how often the
  person stepped in, with the share of tool calls in each bucket. This is the
  main input for the Level 2 versus Level 3 split in Step 4.
- Transcripts are deleted after a retention period, so a window longer than
  about 30 days may be incomplete. Say so if it matters.
- The transcript format is not a stable interface. If the script reports zero
  sessions while files exist under `~/.claude/projects/`, or the numbers look
  implausible, open one transcript, see how it differs from what
  `parse_session()` expects, and adapt. If Python is unavailable, gather the
  same facts by hand: the docstring lists the sources.
- `largest_sessions` includes the first 500 characters of each opening
  prompt. Read these to judge how work is handed over (see L3.3 below). They
  may mention internal or sensitive work, so paraphrase rather than quote
  them in the report.
- Then look past the numbers at two or three of the largest sessions in the
  main work repositories. Skim the transcript itself to see what the person's
  follow-up messages were: corrections of wrong decisions, new requirements,
  or just "continue".

Do not change any files, settings or repositories during the assessment.

**Without file access (chat only)**, say plainly that the result rests on
self-report and will carry lower confidence. If past conversations are
searchable, look at recent engineering ones for how tasks were handed over.
Then run the interview in full.

## Step 2: Confirm the name, check coverage and scope, then interview

**Name first.** Every report must carry the full name of the person assessed,
because reports from the whole team are collected and compared. The collector
gives a guess in `employee` (git `user.name`, then the OS account). Show it
and ask the person to confirm or correct it, in the same message as the
coverage question below. If there is no guess, ask for their full name. Use
the confirmed name exactly as given; do not run the report without one.

**Coverage.** Before interpreting anything, show the person the config
folders and repositories the collector found, with session counts, and ask
what is missing: another seat or config folder, WSL or a container, another
machine. Missing work can change the level, so treat an answer like "I also
use another Claude config" as something to go and read, not as a caveat to
write down:

- **Another folder reachable from here** (a second seat, WSL, a mounted
  drive): ask for its path, check it has a `projects/` folder, and rerun the
  collector with `--config-dir` for it and for each folder already found.
- **Another machine:** ask them to run the collector there and bring the
  output back (`python collect_evidence.py > evidence-<machine>.json`), then
  assess both files together. If they would rather not do that now, carry on.
- **Only if it cannot be read:** ask what share of their work runs there and
  how they work in it, list it under "Not visible", and lower the confidence.
  If it is a large share, call the level provisional.

**Then scope.** People often work differently in a small project of their
own than in their employer's main codebase: a new, small repository is far
easier to delegate in. Show the repositories found and ask which are their
main work. The working level is decided on those. Other repositories (side
projects, experiments, hackathons) are reported in their own line and count
toward reach. If the person says everything is main work, treat it so.

**Then the interview.** Even with every config folder read, local
transcripts miss other tools (Copilot, Cursor) and agents that run in the
cloud or in CI. Ask about
those, and about anything the evidence left ambiguous. Keep it to five
questions at most, skip any the evidence already answers, and ask for recent
concrete instances rather than self-ratings, because "do you delegate to
agents?" gets a yes from everyone. Use a question tool if one is available;
otherwise ask in one message.

1. Apart from Claude Code on this machine, which AI tools do you use for
   engineering, and roughly what share of your work goes through each?
   Include background or cloud agents and anything triggered from CI or
   issues.
2. Take the last substantial piece of work you shipped with AI. What did you
   write before it started, how often did you step in, and how did you check
   the result?
3. When did you last have more than one agent working at the same time? What
   was each doing, and how did the pieces come together?
4. Which checks does AI-produced work pass without you running them by hand
   (tests, CI, hooks, review agents)?
5. Of the code you merged in the last month, roughly what share did you type
   yourself?

If answers conflict with the evidence, say so and ask once. Where they still
conflict, weight the evidence and note the disagreement in the report.

## Step 3: Map evidence to criteria

For each criterion in `references/levels.md`, decide: **habitual** (a normal
part of how they work, seen repeatedly across the window), **occasional**
(seen once or twice), or **not seen**. Signals that usually separate levels:

**Level 1 vs 2.** Level 1 looks like: most AI use is completion or chat
outside an agent, sessions that answer questions or touch one file, a high
self-typed share, few commits carrying an AI trailer. Level 2 looks like:
sessions regularly editing several files for one task, the person's messages
state requirements and then correct specific decisions (L2.3, L2.4).

**Level 2 vs 3.** This is the hardest boundary, and tool-call counts alone do
not settle it. Level 3 is about specification up front and limited
intervention after:

- Opening prompts carry constraints, acceptance criteria and how to validate
  (L3.3), or point to a ticket, spec or handoff document that does. A short
  opener is fine when it points at a real spec, so check what it points to
  (`opened_with_command` shows sessions started from a command or skill). A
  one-line opener followed by fifteen steering messages is Level 2, however
  long the session runs.
- A spec worked out in conversation with the agent and then executed in a
  fresh run counts as specification up front. A spec that only emerges
  through corrections during implementation does not.
- Long runs without a human message, with the agent running tests itself and
  the session ending in a commit or PR (L3.5).
- Work arrives pre-split into pieces an agent can finish alone (L3.2): plan
  mode, task lists, tickets written for agents, background or cloud runs.
- The person's messages supervise ("that fails criterion 2, redo it") rather
  than implement, and the self-typed share is low (L3.4).

**Level 3 vs 4.** Level 4 needs one initiative split across agents that work
at the same time, with the person responsible for how the parts fit:

- Parallel means measured overlap, not the presence of several agents. Use
  `parallel_work`: `windows_with_one_repo_in_2_plus_worktrees_or_branches`
  for parallel sessions, and `most_subagents_running_at_once` for subagents.
  Then check the work was related: one feature across worktrees, branches or
  sibling services (L4.1, L4.2). Parallel sessions on unrelated tasks are
  multitasking at Level 2 or 3.
- **Subagent workflows** need care, because people reasonably describe them
  as "running several agents". A plan executed task by task, each with an
  implementer and then a reviewer subagent, is one stream of work: it is
  strong Level 3 (L3.2, L3.5), not L4.1. It counts toward L4.1 and L4.2 when
  subagents on different workstreams actually ran at once and the person
  owned the decomposition, whether they dispatched the agents themselves or
  approved a plan that a lead agent dispatched.
- Evidence of managing integration: interface contracts agreed first, merge
  ordering, conflict resolution between agent branches (L4.3).
- Gates that run without being asked each time (L4.4): hooks, CI jobs that
  test or review agent output, a workflow in which every task passes a
  reviewer agent as a built-in step. A review the person requests by hand
  each time is good Level 2 or 3 practice but not an established gate. The
  gate has to be in use; check that it fires in transcripts or CI history.

Three traps apply at every boundary:

- **Configured is not practised.** Custom agents, skills, hooks and MCP
  servers show capability. Count them only where usage shows up as well.
- **Once is not a habit.** A single impressive session sets the reach, not
  the working level.
- **Absence of evidence here is not absence.** Before concluding "not seen",
  check whether the behaviour could live somewhere this assessment cannot
  see, and ask.

## Step 4: Decide

- **Working level** is the level that fits the bulk of their work in the
  main-work repositories. Put a number on it: sort the working sessions there
  into the modes (the intervention buckets are the starting point, corrected
  by what the transcripts show) and state the split, by sessions and by share
  of the work, for example "Level 3 in 14 of 22 working sessions, about 60%
  of the work; the rest Level 2". If no mode clearly covers more than half,
  give the lower level as the working level and the higher as reach.
- When repositories differ, say so by name: "Level 3 in A and B, Level 2 in
  C". A level that holds only outside the main work is reach, not the
  working level.
- **Reach** is the highest level with at least two criteria met occasionally
  in real work, in any repository.
- **Confidence** is high when transcripts cover most of their work and agree
  with the interview; medium when a meaningful share happens elsewhere or the
  sample is small; low when the result rests on self-report.

If there is little engineering work in the window (few working sessions, few
commits), say that the sample is too thin for a firm level and give a
provisional one, rather than extrapolating from a handful of sessions.

## Step 5: Report

Lead with the answer and keep the report in the conversation to about one
screen. Use this structure:

```markdown
# AI adoption level: Level N — <level name>

**Employee:** <full name, as confirmed in Step 2>
**Assessed on:** <YYYY-MM-DD>
**Working level:** Level N. <One sentence on why, in terms of how they work.>
**Split in main work:** <Level by share of working sessions and of the work, and by repository where they differ.>
**Outside main work:** <Level seen in side projects, or "nothing separate".>
**Reach:** Level M. <What they did at that level, and how often.>
**Confidence:** high | medium | low. <What it rests on.>
**Based on:** last <D> days, <S> working sessions across <R> repos, <config folders and machines read>, <other sources>.

## Evidence
| Level | Status | Strongest evidence |
|---|---|---|
| 1 Assistance | ... | ... |
| 2 Supervision | ... | ... |
| 3 Delegation | ... | ... |
| 4 Orchestration | ... | ... |

## What separates you from Level N+1
<The specific criteria not yet habitual, each with the observation behind it.>

## Next steps
<Two or three concrete changes, tied to their actual repos and kind of work.>

## Not visible to this assessment
<Tools, machines or work the evidence did not cover.>
```

In the Evidence table, Status is "working level", "habitual", "occasional",
"not seen" or, for levels below the working level, "moved past". Cite
numbers and specific sessions (date, repo, what happened) so the person can
check the claim. If their own view and the evidence disagree, say where in
one short paragraph after the table. For next steps, name a real upcoming or
recent task of theirs and say what doing it one level up would look like;
generic advice such as "try using more agents" gives them nothing to act on.
At Level 4, replace the "separates you" section with where the orchestration
is weakest.

**Saving.** Offer to save the report as a dated markdown file so the next
run can be compared against it. Name it
`ai-adoption-level-<firstname-lastname>-<YYYY-MM-DD>.md` (lowercase, spaces
as hyphens) and keep the Employee line at the top. In the saved file only, add a "Key numbers"
table (working sessions, the intervention split, long runs, tests, PRs,
subagent and parallel-work measures, AI-trailer share of commits, hooks, CI)
and one line on scope: repositories counted as main work and the window. If
an earlier report exists in the place the user names, say what changed since.

**Sharing.** The full report names repositories, tickets and details of the
person's work. If they will pass the result to someone else, offer a short
version instead: employee name, date, working level, split, reach,
confidence and the key numbers, with no repository names, ticket numbers or
prompt content. The employee name always stays in, in every version.
