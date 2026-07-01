---
name: tokenwise
description: >-
  Find out what burned tokens in a Claude Code session and how to cut it. Runs a
  local, zero-dependency analyzer over the session transcript (main + subagents)
  and reports the biggest token drivers — large tool results that re-bill on
  every later turn, redundant file reads, subagent spend, cache efficiency — then
  turns that into concrete, prioritized fixes. Use when the user asks why a
  session/agent was expensive, wants to reduce token or context usage, make an
  agent cheaper, or "what used all my tokens?". Read-only.
---

# tokenwise — where did the tokens go, and how to spend less

Your job: analyze a Claude Code session's token usage and hand back a short,
prioritized list of concrete cuts. The heavy lifting is a local script — you
stay cheap by reading its **compact summary**, never the raw transcript.

> **Do NOT read transcript `.jsonl` files into your context.** They are huge and
> reading them is the exact anti-pattern tokenwise exists to catch. Always go
> through the analyzer script.

## Step 1 — Run the analyzer

The script lives next to this SKILL.md. Resolve this skill's directory and run:

```sh
node <skill-dir>/scripts/analyze.mjs            # auto-detects THIS project's latest session
node <skill-dir>/scripts/analyze.mjs <path>     # a specific .jsonl, or a session dir
```

- No argument → it derives the current project's transcript dir from `cwd`
  (`~/.claude/projects/<escaped-cwd>/`) and analyzes the most recent session
  plus its `subagents/`.
- If the user points at a different session/agent, pass that `.jsonl` file or a
  directory (it recurses into `subagents/`).
- If it can't find a transcript, ask the user for the path, or list candidates
  with `ls -t ~/.claude/projects/*/*.jsonl | head`.

The script prints one compact report — including an **exact session cost in dollars** (from the transcript's `usage` fields, priced per-model, with the 5-minute vs 1-hour cache-write split), a **cache-efficiency grade**, and a **quantified SAVINGS OPPORTUNITIES** section. Read only that.

Pricing rates live at the top of `analyze.mjs` (dated). If the user says rates are stale, point them there.

## Step 2 — Interpret (know what actually costs money)

Ground truth about Claude Code token cost — use it to prioritize:

- **Context compounds.** Every tool result stays in context and is re-sent on
  every later turn (as cache-read, which is cheaper but not free). A big result
  early in a long session is the most expensive thing there is — that's why the
  report ranks by **compounding footprint** (size × turns it stayed), not just
  size.
- **Output tokens are generated fresh** every turn and are the priciest per
  token. Long, repeated preambles/summaries add up.
- **Cache-read share** tells you how well the session cached. High is good; low
  means context kept churning (frequent edits high up, or big new reads).
- **Subagent split** shows spend that happened off your main thread — often the
  real cost sink (a review/search agent doing 30+ tool calls).

## Step 3 — Report: prioritized, concrete, quantified

Return a short report. For each finding, tie it to a number from the analyzer
and give a specific fix. Draw from these levers:

- **Read spans, not whole files** — the top compounding-footprint items are
  usually whole-file `Read`s. Recommend `offset`/`limit` or `grep -n` then a
  targeted read. Quantify: "reading X cost ~N tok-turns; a 40-line span would
  have been ~90% less."
- **Kill redundant reads** — call out any file read ≥2× (the analyzer lists
  them); the file was already in context.
- **Move big/expensive work into subagents** — so its context doesn't ride
  along in the main thread; or, if a subagent itself was heavy, tighten its
  scope and cap its tool calls.
- **Cheaper model where the task allows** — Sonnet/Haiku for mechanical steps.
- **Trim generated output** — avoid re-summarizing; be terse.
- **Prefer `git diff` / heads / `wc`** over dumping full files or command output.

Lead with the **session cost and grade** (the numbers the analyzer computed), then
the prioritized fixes, then end with the **biggest single win** — the one change
that would have saved the most, quoted in **dollars** and token-turns.

Be honest: token estimates are transcript-chars ÷ 4 (the report says so). Don't
present them as exact billing. The point is relative magnitude and direction.
