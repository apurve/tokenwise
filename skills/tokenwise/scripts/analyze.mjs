#!/usr/bin/env node
// tokenwise — analyze a Claude Code session transcript for what burned tokens.
//
// Usage:
//   node analyze.mjs                     # auto-detect current project's latest session
//   node analyze.mjs <transcript.jsonl>  # a specific transcript
//   node analyze.mjs <session-dir>       # a dir of .jsonl files (recurses subagents/)
//
// Zero dependencies. Node 18+. Read-only: it only reads transcript files.
//
// It does the heavy parsing locally and prints a COMPACT report, so the skill
// itself stays cheap — the whole point of tokenwise.

import fs from "node:fs";
import path from "node:path";
import os from "node:os";

const CHARS_PER_TOKEN = 4; // rough estimate; transcripts don't store per-result token counts
const approxTok = (s) => Math.ceil((typeof s === "string" ? s.length : 0) / CHARS_PER_TOKEN);
const fmt = (n) => n.toLocaleString("en-US");
const k = (n) => (n >= 1000 ? (n / 1000).toFixed(1) + "k" : String(n));
const usd = (n) => (n >= 0.01 || n === 0 ? "$" + n.toFixed(2) : "$" + n.toFixed(4));

// Base $/1M-token rates (Anthropic public pricing, as of 2026-07; edit if they change).
// Cache multipliers: write-5m = 1.25x input, write-1h = 2x input, read = 0.1x input.
function rate(model) {
  const m = (model || "").toLowerCase();
  if (m.includes("fable") || m.includes("mythos")) return { in: 10, out: 50 };
  if (m.includes("opus")) return { in: 5, out: 25 };
  if (m.includes("sonnet")) return { in: 3, out: 15 };
  if (m.includes("haiku")) return { in: 1, out: 5 };
  return { in: 5, out: 25 }; // unknown → assume Opus-tier so we never under-report
}
// b = {inp, cc5m, cc1h, cr, out} in tokens; exact $ (usage fields are exact, not estimated)
function costOf(b, model) {
  const r = rate(model);
  return (b.inp * r.in + b.cc5m * r.in * 1.25 + b.cc1h * r.in * 2 + b.cr * r.in * 0.1 + b.out * r.out) / 1e6;
}

// ---------- locate transcript files ----------
function newestJsonlIn(dir) {
  if (!fs.existsSync(dir)) return null;
  const files = fs.readdirSync(dir)
    .filter((f) => f.endsWith(".jsonl"))
    .map((f) => path.join(dir, f))
    .map((p) => ({ p, m: fs.statSync(p).mtimeMs }))
    .sort((a, b) => b.m - a.m);
  return files.length ? files[0].p : null;
}

function resolveTargets(arg) {
  const out = [];
  if (arg) {
    const st = fs.existsSync(arg) ? fs.statSync(arg) : null;
    if (!st) { console.error(`tokenwise: not found: ${arg}`); process.exit(1); }
    if (st.isFile()) return [{ file: arg, kind: "main" }];
    if (st.isDirectory()) {
      const walk = (d) => {
        for (const e of fs.readdirSync(d, { withFileTypes: true })) {
          const fp = path.join(d, e.name);
          if (e.isDirectory()) walk(fp);
          else if (e.name.endsWith(".jsonl")) out.push({ file: fp, kind: fp.includes(`${path.sep}subagents${path.sep}`) ? "subagent" : "main" });
        }
      };
      walk(arg);
      return out;
    }
  }
  // no arg: derive from cwd → ~/.claude/projects/<escaped-cwd>/
  const projectDir = path.join(os.homedir(), ".claude", "projects", process.cwd().replace(/\//g, "-"));
  const main = newestJsonlIn(projectDir);
  if (!main) { console.error(`tokenwise: no transcript found for this project (looked in ${projectDir}). Pass a .jsonl path explicitly.`); process.exit(1); }
  out.push({ file: main, kind: "main" });
  const subDir = path.join(projectDir, path.basename(main, ".jsonl"), "subagents");
  if (fs.existsSync(subDir)) for (const f of fs.readdirSync(subDir)) if (f.endsWith(".jsonl")) out.push({ file: path.join(subDir, f), kind: "subagent" });
  return out;
}

// ---------- extract a human label for a tool call ----------
function targetOf(b) {
  const i = b.input || {};
  switch (b.name) {
    case "Read": case "Edit": case "Write": case "NotebookEdit":
      return i.file_path ? path.basename(i.file_path) : b.name;
    case "Bash":
      return (i.command || i.description || "").slice(0, 48).replace(/\s+/g, " ");
    case "Grep": return `grep ${(i.pattern || "").slice(0, 32)}`;
    case "Glob": return `glob ${(i.pattern || "").slice(0, 32)}`;
    case "Agent": case "Task": return `agent: ${(i.description || "").slice(0, 40)}`;
    case "WebFetch": return `fetch ${(i.url || "").slice(0, 40)}`;
    default: return b.name;
  }
}
function textOf(content) {
  if (typeof content === "string") return content;
  if (Array.isArray(content)) return content.map((c) => (typeof c === "string" ? c : c?.text || "")).join("");
  return "";
}

// ---------- analyze one file ----------
function analyzeFile(file) {
  let lines = [];
  try { lines = fs.readFileSync(file, "utf8").split("\n").filter(Boolean); } catch { return null; }
  let turns = 0, out = 0, inp = 0, cc = 0, cr = 0;
  const models = new Set();
  const byModel = new Map();          // model -> {inp, cc5m, cc1h, cr, out} for exact pricing
  const toolUses = new Map();          // id -> {name, target, turn}
  const toolCalls = new Map();         // name -> {count, resultTok}
  const readCounts = new Map();        // file_path -> count
  const results = [];                  // {name, target, tok, turn}
  for (const line of lines) {
    let o; try { o = JSON.parse(line); } catch { continue; }
    const msg = o.message;
    if (o.type === "assistant" && msg) {
      turns++;
      const u = msg.usage || {};
      out += u.output_tokens || 0; inp += u.input_tokens || 0;
      cc += u.cache_creation_input_tokens || 0; cr += u.cache_read_input_tokens || 0;
      if (msg.model) models.add(msg.model);
      // exact per-model buckets for pricing (cache_creation splits 5m vs 1h)
      const model = msg.model || "unknown";
      const b = byModel.get(model) || { inp: 0, cc5m: 0, cc1h: 0, cr: 0, out: 0 };
      b.inp += u.input_tokens || 0;
      b.cr += u.cache_read_input_tokens || 0;
      b.out += u.output_tokens || 0;
      const ccb = u.cache_creation || {};
      const c5 = ccb.ephemeral_5m_input_tokens || 0, c1 = ccb.ephemeral_1h_input_tokens || 0;
      if (c5 || c1) { b.cc5m += c5; b.cc1h += c1; }
      else { b.cc5m += u.cache_creation_input_tokens || 0; } // no breakdown → assume 5m
      byModel.set(model, b);
      if (Array.isArray(msg.content)) for (const b of msg.content) {
        if (!b || !b.type) continue;
        if (b.type === "tool_use") {
          const target = targetOf(b);
          toolUses.set(b.id, { name: b.name, target, turn: turns });
          const t = toolCalls.get(b.name) || { count: 0, resultTok: 0 };
          t.count++; toolCalls.set(b.name, t);
          if (b.name === "Read" && b.input?.file_path) readCounts.set(b.input.file_path, (readCounts.get(b.input.file_path) || 0) + 1);
        }
      }
    } else if (msg && Array.isArray(msg.content)) {
      for (const b of msg.content) {
        if (b && b.type === "tool_result") {
          const tu = toolUses.get(b.tool_use_id) || {};
          const tok = approxTok(textOf(b.content));
          results.push({ name: tu.name || "?", target: tu.target || "(result)", tok, turn: tu.turn || turns });
          const t = toolCalls.get(tu.name); if (t) t.resultTok += tok;
        }
      }
    }
  }
  return { file, turns, out, inp, cc, cr, models: [...models], byModel, toolUses, toolCalls, readCounts, results };
}

// ---------- main ----------
const targets = resolveTargets(process.argv[2]);
const analyses = targets.map((t) => ({ ...t, a: analyzeFile(t.file) })).filter((x) => x.a);
if (!analyses.length) { console.error("tokenwise: nothing to analyze."); process.exit(1); }

const totalTurns = analyses.reduce((s, x) => s + x.a.turns, 0);
const totalOut = analyses.reduce((s, x) => s + x.a.out, 0);
const totalCC = analyses.reduce((s, x) => s + x.a.cc, 0);
const totalCR = analyses.reduce((s, x) => s + x.a.cr, 0);
const totalInp = analyses.reduce((s, x) => s + x.a.inp, 0);
const mainOut = analyses.filter((x) => x.kind === "main").reduce((s, x) => s + x.a.out, 0);
const subOut = analyses.filter((x) => x.kind === "subagent").reduce((s, x) => s + x.a.out, 0);
const totalProcessed = totalOut + totalInp + totalCC + totalCR;
const cacheHit = totalCR + totalCC + totalInp > 0 ? Math.round((totalCR / (totalCR + totalCC + totalInp)) * 100) : 0;

// exact cost: merge per-model buckets across all files, price each model at its own rate
const modelBuckets = new Map();
for (const x of analyses) for (const [model, b] of x.a.byModel) {
  const cur = modelBuckets.get(model) || { inp: 0, cc5m: 0, cc1h: 0, cr: 0, out: 0 };
  for (const key of ["inp", "cc5m", "cc1h", "cr", "out"]) cur[key] += b[key];
  modelBuckets.set(model, cur);
}
let totalCost = 0;
const costRows = [];
for (const [model, b] of modelBuckets) { const c = costOf(b, model); totalCost += c; costRows.push([model, c]); }
costRows.sort((a, b) => b[1] - a[1]);
const domModel = costRows.length ? costRows[0][0] : "claude-opus-4-8";
const grade = cacheHit >= 90 ? "A" : cacheHit >= 75 ? "B" : cacheHit >= 50 ? "C" : cacheHit >= 25 ? "D" : "F";

// results across files, tagged with per-file turn count for footprint math
const allResults = analyses.flatMap((x) => x.a.results.map((r) => ({ ...r, kind: x.kind, N: x.a.turns, footprint: r.tok * Math.max(0, x.a.turns - r.turn) })));
const bySize = [...allResults].sort((a, b) => b.tok - a.tok).slice(0, 8);
const byFootprint = [...allResults].sort((a, b) => b.footprint - a.footprint).slice(0, 6);

// redundant reads (combined)
const redundant = new Map();
for (const x of analyses) for (const [f, c] of x.a.readCounts) if (c >= 2) redundant.set(f, (redundant.get(f) || 0) + c);

// tool breakdown (combined)
const tools = new Map();
for (const x of analyses) for (const [n, t] of x.a.toolCalls) {
  const cur = tools.get(n) || { count: 0, resultTok: 0 }; cur.count += t.count; cur.resultTok += t.resultTok; tools.set(n, cur);
}
const toolRows = [...tools.entries()].sort((a, b) => b[1].resultTok - a[1].resultTok);

// ---------- print compact report ----------
const L = [];
L.push("TOKENWISE REPORT  (token counts are estimates: transcript chars ÷ 4)");
L.push(`Files analyzed: ${analyses.length}  (main: ${analyses.filter(x=>x.kind==="main").length}, subagents: ${analyses.filter(x=>x.kind==="subagent").length})`);
L.push(`Models: ${[...new Set(analyses.flatMap(x=>x.a.models))].join(", ") || "n/a"}`);
L.push("");
L.push("── TOTALS ──");
L.push(`Assistant turns: ${fmt(totalTurns)}`);
L.push(`Output (generated) tokens: ${fmt(totalOut)}`);
L.push(`Input — uncached: ${fmt(totalInp)} | cache-created: ${fmt(totalCC)} | cache-read: ${fmt(totalCR)}`);
L.push(`Total tokens processed (billed mix): ${fmt(totalProcessed)}   | cache-read share: ${cacheHit}%`);
if (subOut > 0) L.push(`Output split — main session: ${fmt(mainOut)}  |  subagents: ${fmt(subOut)}  (${Math.round(subOut/(totalOut||1)*100)}% went to subagents)`);
L.push("");
L.push("── COST (exact — from usage fields, at listed $/MTok rates) ──");
L.push(`Estimated session cost: ${usd(totalCost)}    |    cache-efficiency grade: ${grade} (${cacheHit}% served from cache)`);
if (costRows.length > 1) costRows.forEach(([m, c]) => L.push(`  • ${m}: ${usd(c)}`));
L.push("");
L.push("── BIGGEST TOOL RESULTS (by size) ── these sit in context and re-bill on every later turn");
bySize.forEach((r, i) => L.push(`${i + 1}. [${r.name}] ${r.target} — ~${k(r.tok)} tok @ turn ${r.turn}/${r.N}${r.kind === "subagent" ? " (subagent)" : ""}`));
L.push("");
L.push("── HIGHEST COMPOUNDING FOOTPRINT ── size × turns it stayed in context (the real cost driver)");
byFootprint.forEach((r, i) => L.push(`${i + 1}. [${r.name}] ${r.target} — ~${k(r.tok)} tok × ${Math.max(0, r.N - r.turn)} turns ≈ ${k(r.footprint)} tok-turns`));
L.push("");
if (redundant.size) {
  L.push("── REDUNDANT READS (same file read ≥2×) ──");
  [...redundant.entries()].sort((a, b) => b[1] - a[1]).slice(0, 8).forEach(([f, c]) => L.push(`• ${path.basename(f)} — ${c}× (${f})`));
  L.push("");
}
L.push("── TOOL BREAKDOWN (by result tokens pulled into context) ──");
toolRows.forEach(([n, t]) => L.push(`• ${n}: ${t.count} calls, ~${k(t.resultTok)} tok of results`));
L.push("");
// ---------- quantified savings opportunities (the part that makes it actionable) ----------
const readRate = rate(domModel).in * 0.1; // $/MTok when a block is re-read from cache
const opps = [];

// 1) whole-file Reads that rode along in context — reclaim ~90% by reading spans
const readFootprint = allResults.filter((r) => r.name === "Read").reduce((s, r) => s + r.footprint, 0);
const spanSave = (readFootprint * readRate / 1e6) * 0.9;
if (spanSave > 0.01) opps.push(`Read spans, not whole files: ~${usd(spanSave)} reclaimable. Big early Reads re-bill as cache-reads every later turn; offset/limit or grep-then-read the relevant lines.`);

// 2) premium-model subagents that could run on Sonnet
let downgradeSave = 0;
for (const x of analyses) if (x.kind === "subagent") for (const [model, b] of x.a.byModel) {
  const m = model.toLowerCase();
  if (m.includes("opus") || m.includes("fable")) { const s = costOf(b, model) - costOf(b, "claude-sonnet-4-6"); if (s > 0) downgradeSave += s; }
}
if (downgradeSave > 0.01) opps.push(`Cheaper model for subagents: ~${usd(downgradeSave)} if the premium-model subagent work ran on Sonnet. Reserve Opus/Fable for the reasoning that needs it.`);

// 3) redundant reads — you already had the file in context
if (redundant.size) {
  const reReadTok = [...redundant.values()].reduce((s, c) => s + c, 0) * 200; // rough: ~200 tok/dup avg
  opps.push(`Kill ${redundant.size} redundant re-read${redundant.size > 1 ? "s" : ""}: the file was already in context after the first Read.`);
}

if (byFootprint.length) {
  const top = byFootprint[0];
  const wasteUsd = (top.footprint * readRate) / 1e6;
  L.push("── BIGGEST WIN ──");
  L.push(`Reading [${top.name}] ${top.target} early cost ~${usd(wasteUsd)} in cache re-reads (~${k(top.footprint)} tok-turns).`);
  L.push(`Reading only the needed span (say ~10% of it) would reclaim most of that. Multiply across every long session.`);
  L.push("");
}
if (opps.length) {
  L.push("── SAVINGS OPPORTUNITIES (quantified) ──");
  opps.forEach((o, i) => L.push(`${i + 1}. ${o}`));
  L.push("");
}
L.push("── FILES ──");
analyses.forEach((x) => L.push(`• ${x.kind}: ${path.basename(x.file)} — ${x.a.turns} turns, out ${k(x.a.out)}`));
console.log(L.join("\n"));
