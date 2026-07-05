import abc
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

class PlatformProvider(abc.ABC):
    """
    Abstract base class for platform-specific transcript parsing.
    """
    
    @abc.abstractmethod
    def get_default_project_dir(self) -> Path:
        """
        Return the default transcript directory for the current project context.
        """
        pass

    @abc.abstractmethod
    def resolve_sessions(self, target: Optional[str]) -> Tuple[str, List[Dict[str, Any]]]:
        """
        Given a target (or None to auto-detect), return a tuple of:
        - The project directory string
        - A list of session objects, each containing:
            {
                "id": str,
                "files": [
                    {"file": str, "kind": "main" | "subagent"}
                ]
            }
        """
        pass

    @abc.abstractmethod
    def resolve_targets(self, target: Optional[str]) -> List[Dict[str, Any]]:
        """
        Given a target path (or None to auto-detect the newest session),
        return a list of targets to analyze:
        [
            {"file": "path/to/main.jsonl", "kind": "main"},
            {"file": "path/to/sub.jsonl", "kind": "subagent"}
        ]
        """
        pass

    @abc.abstractmethod
    def analyze_file(self, file_path: str) -> Optional[Dict[str, Any]]:
        """
        Parse a specific transcript file and return aggregated stats:
        {
            "file": file_path,
            "turns": int,
            "out": int (output tokens),
            "inp": int (input tokens uncached),
            "cc": int (cache creation tokens),
            "cr": int (cache read tokens),
            "firstTs": str,
            "lastTs": str,
            "models": List[str],
            "byModel": {
                "model_name": {"inp": int, "cc5m": int, "cc1h": int, "cr": int, "out": int}
            },
            "toolCalls": {
                "ToolName": {"count": int, "resultTok": int}
            },
            "readCounts": {
                "file_path": int
            },
            "results": [
                {"name": str, "target": str, "tok": int, "turn": int}
            ]
        }
        Returns None if file cannot be read or parsed.
        """
        pass
