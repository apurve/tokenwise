import unittest
import json
import tempfile
from pathlib import Path
from platforms.claude import ClaudePlatform

class TestClaudePlatform(unittest.TestCase):
    def test_analyze_file(self):
        # Create a mock claude transcript
        content = [
            {"timestamp": "2026-07-05T12:00:00Z", "type": "user", "message": {"content": "hello"}},
            {
                "timestamp": "2026-07-05T12:00:01Z", 
                "type": "assistant",
                "message": {
                    "model": "claude-3-5-sonnet-20240620",
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cache_creation_input_tokens": 0,
                        "cache_read_input_tokens": 0
                    },
                    "content": [
                        {"type": "text", "text": "Sure, reading."},
                        {"type": "tool_use", "id": "call_1", "name": "Read", "input": {"file_path": "/test/file.txt"}}
                    ]
                }
            },
            {
                "timestamp": "2026-07-05T12:00:02Z",
                "type": "user",
                "message": {
                    "content": [
                        {"type": "tool_result", "tool_use_id": "call_1", "content": "hello world from file"}
                    ]
                }
            }
        ]
        
        with tempfile.NamedTemporaryFile("w+", suffix=".jsonl", delete=False) as f:
            for c in content:
                f.write(json.dumps(c) + "\n")
            path = f.name
            
        try:
            platform = ClaudePlatform()
            res = platform.analyze_file(path)
            
            self.assertIsNotNone(res)
            self.assertEqual(res["turns"], 1)
            self.assertEqual(res["out"], 50)
            self.assertEqual(res["inp"], 100)
            self.assertEqual(len(res["results"]), 1)
            self.assertEqual(res["results"][0]["name"], "Read")
            self.assertEqual(res["results"][0]["target"], "file.txt")
        finally:
            Path(path).unlink()

if __name__ == '__main__':
    unittest.main()
