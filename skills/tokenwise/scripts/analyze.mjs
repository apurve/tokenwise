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
  return { file, turns, out, inp, cc, cr, models: [...models], toolUses, toolCalls, readCounts, results };
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
L.push("── FILES ──");
analyses.forEach((x) => L.push(`• ${x.kind}: ${path.basename(x.file)} — ${x.a.turns} turns, out ${k(x.a.out)}`));
console.log(L.join("\n"));
