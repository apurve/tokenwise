import os
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from .base import PlatformProvider
from .utils import approx_tok, target_of, text_of

class ClaudePlatform(PlatformProvider):
    
    def get_default_project_dir(self) -> Path:
        cwd_str = os.getcwd().replace(os.sep, "-")
        return Path.home() / ".claude" / "projects" / cwd_str

    def _newest_jsonl_in(self, d: Path) -> Optional[Path]:
        if not d.exists() or not d.is_dir():
            return None
        files = list(d.glob("*.jsonl"))
        if not files:
            return None
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return files[0]

    def _subagents_of(self, project_dir: Path, main_file: Path) -> List[Dict[str, Any]]:
        d = project_dir / main_file.stem / "subagents"
        if not d.exists() or not d.is_dir():
            return []
        return [{"file": str(f), "kind": "subagent"} for f in d.glob("*.jsonl")]

    def resolve_sessions(self, target: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
        p = Path(target) if target else self.get_default_project_dir()
        if not p.exists() or not p.is_dir():
            raise FileNotFoundError(f"tokenwise: project dir not found: {p}")
        
        sessions = []
        for f in p.glob("*.jsonl"):
            sessions.append({
                "id": f.stem,
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
                kind = "subagent" if "subagents" in fp.parts else "main"
                out.append({"file": str(fp), "kind": kind})
            return out
        
        project_dir = self.get_default_project_dir()
        main = self._newest_jsonl_in(project_dir)
        if not main:
            raise FileNotFoundError(f"tokenwise: no transcript for this project (looked in {project_dir}). Pass a .jsonl path.")
        
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
        
        for line in lines:
            try:
                o = json.loads(line)
            except json.JSONDecodeError:
                continue
                
            ts = o.get("timestamp")
            if ts:
                if not first_ts:
                    first_ts = ts
                last_ts = ts
                
            msg = o.get("message")
            if o.get("type") == "assistant" and msg:
                turns += 1
                u = msg.get("usage", {})
                out_tok += u.get("output_tokens", 0)
                inp_tok += u.get("input_tokens", 0)
                cc += u.get("cache_creation_input_tokens", 0)
                cr += u.get("cache_read_input_tokens", 0)
                
                model = msg.get("model", "unknown")
                models.add(model)
                
                b = by_model.setdefault(model, {"inp": 0, "cc5m": 0, "cc1h": 0, "cr": 0, "out": 0})
                b["inp"] += u.get("input_tokens", 0)
                b["cr"] += u.get("cache_read_input_tokens", 0)
                b["out"] += u.get("output_tokens", 0)
                
                ccb = u.get("cache_creation", {})
                c5 = ccb.get("ephemeral_5m_input_tokens", 0)
                c1 = ccb.get("ephemeral_1h_input_tokens", 0)
                if c5 or c1:
                    b["cc5m"] += c5
                    b["cc1h"] += c1
                else:
                    b["cc5m"] += u.get("cache_creation_input_tokens", 0)
                    
                content = msg.get("content", [])
                if isinstance(content, list):
                    for blk in content:
                        if not blk or not isinstance(blk, dict) or "type" not in blk:
                            continue
                        if blk.get("type") == "tool_use":
                            tool_uses[blk.get("id")] = {"name": blk.get("name"), "target": target_of(blk), "turn": turns}
                            t = tool_calls.setdefault(blk.get("name"), {"count": 0, "resultTok": 0})
                            t["count"] += 1
                            if blk.get("name") == "Read":
                                fp = blk.get("input", {}).get("file_path")
                                if fp:
                                    read_counts[fp] = read_counts.get(fp, 0) + 1
                                    
            elif msg and isinstance(msg.get("content"), list):
                for blk in msg["content"]:
                    if blk and isinstance(blk, dict) and blk.get("type") == "tool_result":
                        tu = tool_uses.get(blk.get("tool_use_id"), {})
                        tok = approx_tok(text_of(blk.get("content")))
                        results.append({
                            "name": tu.get("name", "?"),
                            "target": tu.get("target", "(result)"),
                            "tok": tok,
                            "turn": tu.get("turn", turns)
                        })
                        t_name = tu.get("name")
                        if t_name in tool_calls:
                            tool_calls[t_name]["resultTok"] += tok
                            
        return {
            "file": file_path, "turns": turns, "out": out_tok, "inp": inp_tok, "cc": cc, "cr": cr,
            "firstTs": first_ts, "lastTs": last_ts, "models": list(models),
            "byModel": by_model, "toolCalls": tool_calls, "readCounts": read_counts, "results": results
        }
