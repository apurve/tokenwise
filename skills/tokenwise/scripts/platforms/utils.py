import math
from pathlib import Path

CHARS_PER_TOKEN = 4

def approx_tok(s):
    if not isinstance(s, str):
        return 0
    return math.ceil(len(s) / CHARS_PER_TOKEN)

def target_of(b):
    i = b.get("input", {})
    name = b.get("name")
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        fp = i.get("file_path")
        return Path(fp).name if fp else name
    if name == "Bash":
        cmd = i.get("command") or i.get("description") or ""
        return " ".join(cmd[:48].split())
    if name == "Grep":
        return f"grep {(i.get('pattern') or '')[:32]}"
    if name == "Glob":
        return f"glob {(i.get('pattern') or '')[:32]}"
    if name in ("Agent", "Task"):
        return f"agent: {(i.get('description') or '')[:40]}"
    if name == "WebFetch":
        return f"fetch {(i.get('url') or '')[:40]}"
    return name

def text_of(c):
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        out = []
        for x in c:
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict) and "text" in x:
                out.append(x["text"])
        return "".join(out)
    return ""

def fmt(n):
    return f"{n:,}"

def k_fmt(n):
    if n >= 1000:
        return f"{(n / 1000):.1f}k"
    return str(n)

def usd(n):
    if n >= 0.01 or n == 0:
        return f"${n:.2f}"
    return f"${n:.4f}"
