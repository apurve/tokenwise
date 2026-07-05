#!/usr/bin/env python3
# tokenwise — analyze AI agent session transcripts for what burned tokens & dollars.
#
# Usage:
#   python3 analyze.py [--platform name]                     # this project's latest session
#   python3 analyze.py [--platform name] <transcript.jsonl>  # a specific session transcript
#   python3 analyze.py [--platform name] <session-dir>       # a dir of logs
#   python3 analyze.py [--platform name] --trend             # cost across ALL sessions
#   python3 analyze.py [--platform name] --json [target]     # machine-readable output

import os
import sys
import json
from pathlib import Path

from platforms import get_platform
from platforms.utils import approx_tok, k_fmt, fmt, usd

def rate(model):
    m = (model or "").lower()
    if "fable" in m or "mythos" in m:
        return {"in": 10, "out": 50}
    if "opus" in m:
        return {"in": 5, "out": 25}
    if "sonnet" in m:
        return {"in": 3, "out": 15}
    if "haiku" in m:
        return {"in": 1, "out": 5}
    if "pro" in m:  # e.g., gemini-pro
        return {"in": 1.25, "out": 3.75}
    if "flash" in m:
        return {"in": 0.075, "out": 0.3}
    return {"in": 5, "out": 25} # unknown -> assume Opus-tier

def cost_of(b, model):
    r = rate(model)
    return (b['inp'] * r['in'] + b['cc5m'] * r['in'] * 1.25 + b['cc1h'] * r['in'] * 2 + b['cr'] * r['in'] * 0.1 + b['out'] * r['out']) / 1e6

def grade_of(hit):
    if hit >= 90: return "A"
    if hit >= 75: return "B"
    if hit >= 50: return "C"
    if hit >= 25: return "D"
    return "F"

argv = sys.argv[1:]
JSON_OUT = "--json" in argv
TREND = "--trend" in argv

# Parse --platform flag
platform_name = None
if "--platform" in argv:
    idx = argv.index("--platform")
    if idx + 1 < len(argv):
        platform_name = argv[idx + 1]
        argv = argv[:idx] + argv[idx+2:]

target = next((a for a in argv if not a.startswith("--")), None)
provider = get_platform(platform_name)

def aggregate(lst):
    A = []
    for t in lst:
        a = provider.analyze_file(t["file"])
        if a:
            A.append({"file": t["file"], "kind": t["kind"], "a": a})
            
    turns = sum(x["a"]["turns"] for x in A)
    out_tok = sum(x["a"]["out"] for x in A)
    inp_tok = sum(x["a"]["inp"] for x in A)
    cc = sum(x["a"]["cc"] for x in A)
    cr = sum(x["a"]["cr"] for x in A)
    processed = out_tok + inp_tok + cc + cr
    
    cache_hit = round((cr / (cr + cc + inp_tok)) * 100) if (cr + cc + inp_tok) > 0 else 0
    main_out = sum(x["a"]["out"] for x in A if x["kind"] == "main")
    sub_out = sum(x["a"]["out"] for x in A if x["kind"] == "subagent")
    
    model_buckets = {}
    for x in A:
        for model, b in x["a"]["byModel"].items():
            cur = model_buckets.setdefault(model, {"inp": 0, "cc5m": 0, "cc1h": 0, "cr": 0, "out": 0})
            for key in ["inp", "cc5m", "cc1h", "cr", "out"]:
                cur[key] += b[key]
                
    cost = 0
    cost_rows = []
    for model, b in model_buckets.items():
        c = cost_of(b, model)
        cost += c
        cost_rows.append((model, c))
    cost_rows.sort(key=lambda x: x[1], reverse=True)
    dom_model = cost_rows[0][0] if cost_rows else "claude-opus-4-8"
    
    all_results = []
    for x in A:
        for r in x["a"]["results"]:
            r_copy = r.copy()
            r_copy["kind"] = x["kind"]
            r_copy["N"] = x["a"]["turns"]
            r_copy["footprint"] = r["tok"] * max(0, x["a"]["turns"] - r["turn"])
            all_results.append(r_copy)
            
    redundant = {}
    for x in A:
        for f, c in x["a"]["readCounts"].items():
            if c >= 2:
                redundant[f] = redundant.get(f, 0) + c
                
    tools = {}
    for x in A:
        for n, t in x["a"]["toolCalls"].items():
            cur = tools.setdefault(n, {"count": 0, "resultTok": 0})
            cur["count"] += t["count"]
            cur["resultTok"] += t["resultTok"]
            
    first_ts_list = sorted([x["a"]["firstTs"] for x in A if x["a"]["firstTs"]])
    last_ts_list = sorted([x["a"]["lastTs"] for x in A if x["a"]["lastTs"]])
    
    first_ts = first_ts_list[0] if first_ts_list else ""
    last_ts = last_ts_list[-1] if last_ts_list else ""
    
    models = list(set(m for x in A for m in x["a"]["models"]))
    
    return {
        "A": A, "turns": turns, "out": out_tok, "inp": inp_tok, "cc": cc, "cr": cr, 
        "processed": processed, "cacheHit": cache_hit, "grade": grade_of(cache_hit),
        "mainOut": main_out, "subOut": sub_out, "cost": cost, "costRows": cost_rows,
        "domModel": dom_model, "allResults": all_results, "redundant": redundant,
        "tools": tools, "firstTs": first_ts, "lastTs": last_ts, "models": models
    }

if TREND:
    d, sessions = provider.resolve_sessions(target)
    rows = []
    for s in sessions:
        g = aggregate(s["files"])
        if g["turns"] > 0:
            rows.append({
                "id": s["id"], "cost": g["cost"], "turns": g["turns"], "out": g["out"],
                "grade": g["grade"], "cacheHit": g["cacheHit"], "lastTs": g["lastTs"]
            })
    
    total = sum(r["cost"] for r in rows)
    if JSON_OUT:
        print(json.dumps({
            "project_dir": d, "sessions": rows, "total_cost_usd": round(total, 4),
            "currency": "USD", "note": "token rates as of 2026-07; edit analyze.py to change"
        }, indent=2))
        sys.exit(0)
        
    rows.sort(key=lambda r: r["lastTs"] or "", reverse=True)
    L = []
    L.append(f"TOKENWISE TREND  —  {len(rows)} sessions in {d}")
    L.append(f"Total across all sessions: {usd(total)}   (token rates as of 2026-07)")
    L.append("")
    L.append("date        cost      turns  grade  session")
    for r in rows[:40]:
        date = (r["lastTs"] or "")[:10] or "?         "
        L.append(f"{date}  {usd(r['cost']):>8}  {str(r['turns']):>5}  {r['grade']:>5}  {r['id'][:8]}")
    if len(rows) > 40:
        L.append(f"… and {len(rows) - 40} more sessions")
    L.append("")
    if rows:
        top = sorted(rows, key=lambda x: x["cost"], reverse=True)[0]
        L.append(f"Most expensive session: {usd(top['cost'])} ({top['turns']} turns, grade {top['grade']}) — {top['id'][:8]}")
    
    print("\n".join(L))
    sys.exit(0)

g = aggregate(provider.resolve_targets(target))
if not g["A"]:
    print("tokenwise: nothing to analyze.", file=sys.stderr)
    sys.exit(1)

read_rate = rate(g["domModel"])["in"] * 0.1
by_size = sorted(g["allResults"], key=lambda x: x["tok"], reverse=True)[:8]
by_footprint = sorted(g["allResults"], key=lambda x: x["footprint"], reverse=True)[:6]

opps = []
read_footprint = sum(r["footprint"] for r in g["allResults"] if r["name"] in ("Read", "read_file", "view_file"))
span_save = (read_footprint * read_rate / 1e6) * 0.9
if span_save > 0.01:
    opps.append(f"Read spans, not whole files: ~{usd(span_save)} reclaimable. Big early Reads re-bill as cache-reads every later turn; offset/limit or grep-then-read the relevant lines.")

downgrade_save = 0
for x in g["A"]:
    if x["kind"] == "subagent":
        for model, b in x["a"]["byModel"].items():
            m = model.lower()
            if "opus" in m or "fable" in m:
                s = cost_of(b, model) - cost_of(b, "claude-sonnet-4-6")
                if s > 0: downgrade_save += s
                
if downgrade_save > 0.01:
    opps.append(f"Cheaper model for subagents: ~{usd(downgrade_save)} if the premium-model subagent work ran on Sonnet. Reserve Opus/Fable for reasoning that needs it.")

if g["redundant"]:
    s_suffix = "s" if len(g["redundant"]) > 1 else ""
    opps.append(f"Kill {len(g['redundant'])} redundant re-read{s_suffix}: the file was already in context after the first Read.")

if JSON_OUT:
    print(json.dumps({
        "files": len(g["A"]), "models": g["models"],
        "cost_usd": round(g["cost"], 4),
        "cost_by_model": [{"model": m, "usd": round(c, 4)} for m, c in g["costRows"]],
        "cache_grade": g["grade"], "cache_read_share_pct": g["cacheHit"],
        "turns": g["turns"], "output_tokens": g["out"], "input_uncached": g["inp"],
        "cache_created": g["cc"], "cache_read": g["cr"],
        "main_output": g["mainOut"], "subagent_output": g["subOut"],
        "top_by_footprint": [{"tool": r["name"], "target": r["target"], "tokens": r["tok"], "turn": r["turn"], "of": r["N"], "footprint_tok_turns": r["footprint"]} for r in by_footprint],
        "redundant_reads": [{"file": f, "count": c} for f, c in g["redundant"].items()],
        "savings_opportunities": opps,
        "note": "token counts for tool-result sizes are chars/4 estimates; usage-field totals & cost are exact. rates as of 2026-07."
    }, indent=2))
    sys.exit(0)

L = []
L.append(f"TOKENWISE REPORT  (platform: {provider.__class__.__name__})")
main_count = len([x for x in g["A"] if x["kind"] == "main"])
sub_count = len([x for x in g["A"] if x["kind"] == "subagent"])
L.append(f"Files analyzed: {len(g['A'])}  (main: {main_count}, subagents: {sub_count})")
L.append(f"Models: {', '.join(g['models']) if g['models'] else 'n/a'}")
L.append("")
L.append("── TOTALS ──")
L.append(f"Assistant turns: {fmt(g['turns'])}")
L.append(f"Output (generated) tokens: {fmt(g['out'])}")
L.append(f"Input — uncached: {fmt(g['inp'])} | cache-created: {fmt(g['cc'])} | cache-read: {fmt(g['cr'])}")
L.append(f"Total tokens processed (billed mix): {fmt(g['processed'])}   | cache-read share: {g['cacheHit']}%")
if g["subOut"] > 0:
    pct = round(g["subOut"] / (g["out"] or 1) * 100)
    L.append(f"Output split — main: {fmt(g['mainOut'])}  |  subagents: {fmt(g['subOut'])}  ({pct}% went to subagents)")
L.append("")
L.append("── COST (exact — from usage fields, at listed $/MTok rates) ──")
L.append(f"Estimated session cost: {usd(g['cost'])}    |    cache-efficiency grade: {g['grade']} ({g['cacheHit']}% served from cache)")
if len(g["costRows"]) > 1:
    for m, c in g["costRows"]:
        L.append(f"  • {m}: {usd(c)}")
L.append("")
L.append("── BIGGEST TOOL RESULTS (by size) ── these sit in context and re-bill on every later turn")
for i, r in enumerate(by_size):
    sub = " (subagent)" if r["kind"] == "subagent" else ""
    L.append(f"{i+1}. [{r['name']}] {r['target']} — ~{k_fmt(r['tok'])} tok @ turn {r['turn']}/{r['N']}{sub}")
L.append("")
L.append("── HIGHEST COMPOUNDING FOOTPRINT ── size × turns it stayed in context (the real cost driver)")
for i, r in enumerate(by_footprint):
    turns_stayed = max(0, r["N"] - r["turn"])
    L.append(f"{i+1}. [{r['name']}] {r['target']} — ~{k_fmt(r['tok'])} tok × {turns_stayed} turns ≈ {k_fmt(r['footprint'])} tok-turns")
L.append("")
if g["redundant"]:
    L.append("── REDUNDANT READS (same file read ≥2×) ──")
    sorted_redundant = sorted(g["redundant"].items(), key=lambda x: x[1], reverse=True)[:8]
    for f, c in sorted_redundant:
        L.append(f"• {Path(f).name} — {c}× ({f})")
    L.append("")
L.append("── TOOL BREAKDOWN (by result tokens pulled into context) ──")
sorted_tools = sorted(g["tools"].items(), key=lambda x: x[1]["resultTok"], reverse=True)
for n, t in sorted_tools:
    L.append(f"• {n}: {t['count']} calls, ~{k_fmt(t['resultTok'])} tok of results")
L.append("")
if by_footprint:
    top = by_footprint[0]
    waste_usd = (top["footprint"] * read_rate) / 1e6
    L.append("── BIGGEST WIN ──")
    L.append(f"Reading [{top['name']}] {top['target']} early cost ~{usd(waste_usd)} in cache re-reads (~{k_fmt(top['footprint'])} tok-turns).")
    L.append("Reading only the needed span (say ~10% of it) would reclaim most of that. Multiply across every long session.")
    L.append("")
if opps:
    L.append("── SAVINGS OPPORTUNITIES (quantified) ──")
    for i, o in enumerate(opps):
        L.append(f"{i+1}. {o}")
    L.append("")
L.append("── FILES ──")
for x in g["A"]:
    L.append(f"• {x['kind']}: {Path(x['file']).name} — {x['a']['turns']} turns, out {k_fmt(x['a']['out'])}")
    
print("\n".join(L))
