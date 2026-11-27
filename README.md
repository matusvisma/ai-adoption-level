# AI adoption level check

A Claude Code plugin that works out which AI adoption level you actually work
at (1 Assistance, 2 Supervision, 3 Delegation, 4 Orchestration). It reads your
local Claude Code session history, configuration and git history, asks a few
questions, and reports your level with the evidence and what the next level
would take.

Everything runs on your machine. The plugin only reads; it changes no files
and sends nothing anywhere beyond your normal Claude Code session.

## Install

In a Claude Code session:

```
/plugin marketplace add matusvisma/ai-adoption-level
/plugin install ai-adoption@ai-adoption-tools
```

For a host other than github.com, use the full git URL in the first command.

## Run

Open Claude Code in a repository you work in and run:

```
/ai-adoption:level
```

Python 3 is needed for the evidence script. Without it, Claude gathers the
same facts by hand, which is slower.

## Update

```
/plugin marketplace update ai-adoption-tools
```

Or turn on auto-update: `/plugin` > Marketplaces > ai-adoption-tools >
Enable auto-update.

## Change the level definitions

Edit `plugins/ai-adoption/skills/level/references/levels.md` and push.

## Test

The evidence collector has tests built on synthetic transcripts. They use
only the standard library. From the repository root:

```
python -m unittest discover -s tests
```
