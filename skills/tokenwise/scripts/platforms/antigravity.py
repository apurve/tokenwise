import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from .base import PlatformProvider
from .utils import approx_tok, target_of, text_of

class AntigravityPlatform(PlatformProvider):
    
    def get_default_project_dir(self) -> Path:
        # Default to ~/.gemini/antigravity-ide/brain/
        return Path.home() / ".gemini" / "antigravity-ide" / "brain"

    def _newest_jsonl_in(self, d: Path) -> Optional[Path]:
        if not d.exists() or not d.is_dir():
            return None
        # We need to look in brain/<uuid>/.system_generated/logs/transcript.jsonl
        # Let's find the newest transcript.jsonl
        files = list(d.rglob("transcript.jsonl"))
        if not files:
            return None
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0]

    def _subagents_of(self, project_dir: Path, main_file: Path) -> List[Dict[str, Any]]:
        # Antigravity does have subagents, often in subdirectories or different conversations.
        # For now, we'll return an empty list or try to guess.
        return []

    def resolve_sessions(self, target: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
        p = Path(target) if target else self.get_default_project_dir()
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(f"tokenwise: project dir not found: {p}")
        
        sessions = []
        for f in p.rglob("transcript.jsonl"):
            # Use the conversation ID as the session ID (parent.parent.parent name)
            conv_id = f.parent.parent.parent.name
            sessions.append({
                "id": conv_id,
                "files": [{"file": str(f), "kind": "main"}] + self._subagents_of(p, f)
            })
        return str(p), sessions

    def resolve_targets(self, target: Optional[str]) -> List[Dict[str, Any]]:
        if target:
            p = Path(target)
            if not p.exists():
                raise FileNotFoundError(f"tokenwise: not found: {target}")
            if p.is_file():
                return [{"file": str(p), "kind": "main"}]
            out = []
            for fp in p.rglob("*.jsonl"):
                out.append({"file": str(fp), "kind": "main"})
            return out
        
        project_dir = self.get_default_project_dir()
        main = self._newest_jsonl_in(project_dir)
        if not main:
            raise FileNotFoundError(f"tokenwise: no transcript found in {project_dir}")
        
        return [{"file": str(main), "kind": "main"}] + self._subagents_of(project_dir, main)

    def analyze_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                lines = [line.strip() for line in f if line.strip()]
        except Exception:
            return None
            
        turns = 0; out_tok = 0; inp_tok = 0; cc = 0; cr = 0
        first_ts = ""; last_ts = ""
        models = set()
        by_model = {}
        tool_uses = {}
        tool_calls = {}
        read_counts = {}
        results = []
        
        # Antigravity doesn't explicitly log token usage in standard transcripts.
        # We will estimate tokens using chars/4 heuristic for inputs and outputs.
        model = "gemini-pro"
        models.add(model)
        b = by_model.setdefault(model, {"inp": 0, "cc5m": 0, "cc1h": 0, "cr": 0, "out": 0})
        
        for line in lines:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            ts = o.get("created_at")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
                
            src = o.get("source")
            typ = o.get("type")
            
            if src == "MODEL" and typ == "PLANNER_RESPONSE":
                turns += 1
                content_tok = approx_tok(o.get("content", ""))
                out_tok += content_tok
                b["out"] += content_tok
                
                t_calls = o.get("tool_calls", [])
                for idx, call in enumerate(t_calls):
                    name = call.get("name")
                    args = call.get("args", {})
                    target = name
                    if name == "view_file":
                        target = Path(args.get("AbsolutePath", "")).name
                    elif name == "run_command":
                        target = " ".join(args.get("CommandLine", "")[:48].split())
                    elif name == "grep_search":
                        target = f"grep {args.get('Query', '')[:32]}"
                    
                    call_id = f"call_{o.get('step_index')}_{idx}"
                    tool_uses[call_id] = {"name": name, "target": target, "turn": turns}
                    t = tool_calls.setdefault(name, {"count": 0, "resultTok": 0})
                    t["count"] += 1
                    
                    if name == "view_file" or name == "read_file":
                        fp = args.get("AbsolutePath")
                        if fp:
                            read_counts[fp] = read_counts.get(fp, 0) + 1

            elif src == "USER_EXPLICIT" or src == "SYSTEM":
                # Estimate input tokens
                inp = approx_tok(o.get("content", ""))
                inp_tok += inp
                b["inp"] += inp
                
            elif src == "MODEL" and typ not in ("PLANNER_RESPONSE",):
                # This could be a tool response
                # Let's match it to the previous planner response if possible
                # Antigravity sets type to the tool name sometimes, or just has content
                # For simplicity, we just log it as a result.
                name = typ
                call_id = f"call_{o.get('step_index', 0)-1}_0" # Rough guess
                tu = tool_uses.get(call_id, {"name": name, "target": name, "turn": turns})
                tok = approx_tok(o.get("content", ""))
                
                # Approximate input accumulation since context compounds
                # We'll just add it to input tokens for heuristic purposes
                b["inp"] += tok
                inp_tok += tok
                
                if tok > 0:
                    results.append({
                        "name": tu.get("name", "?"),
                        "target": tu.get("target", "(result)"),
                        "tok": tok,
                        "turn": tu.get("turn", turns)
                    })
                    if tu.get("name") in tool_calls:
                        tool_calls[tu.get("name")]["resultTok"] += tok
                            
        return {
            "file": file_path, "turns": turns, "out": out_tok, "inp": inp_tok, "cc": cc, "cr": cr,
            "firstTs": first_ts, "lastTs": last_ts, "models": list(models),
            "byModel": by_model, "toolCalls": tool_calls, "readCounts": read_counts, "results": results
        }
