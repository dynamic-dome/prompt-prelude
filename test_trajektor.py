import json
import os
import pytest

import trajektor as tj


class TestWindowState:
    def test_load_window_default(self, tmp_path):
        w = tj.load_window("s1", str(tmp_path))
        assert w == {"calls": [], "call_count": 0, "armed": True,
                     "cooldown_until": 0, "fires": 0}

    def test_roundtrip(self, tmp_path):
        w = tj.load_window("s1", str(tmp_path))
        w = tj.update_window(w, "Read", ["c:/x/a.py"])
        tj.save_window("s1", str(tmp_path), w)
        again = tj.load_window("s1", str(tmp_path))
        assert again["call_count"] == 1 and again["calls"][0]["tool"] == "Read"

    def test_window_caps_at_k(self, tmp_path):
        w = tj.load_window("s1", str(tmp_path))
        for i in range(20):
            w = tj.update_window(w, "Read", [f"c:/x/{i}.py"])
        assert len(w["calls"]) == tj.WINDOW_K == 15
        assert w["call_count"] == 20

    def test_corrupt_window_resets(self, tmp_path):
        p = tj.window_path("s1", str(tmp_path))
        os.makedirs(str(tmp_path), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write("{kaputt")
        assert tj.load_window("s1", str(tmp_path))["calls"] == []


class TestExtractToolPaths:
    def test_file_path_input(self):
        assert tj.extract_tool_paths("Edit", {"file_path": r"C:\repo\app\main.py"}) \
            == ["c:/repo/app/main.py"]

    def test_bash_command_tokens(self):
        paths = tj.extract_tool_paths("Bash", {"command": "python -m pytest tests/test_x.py -q"})
        assert "tests/test_x.py" in paths

    def test_grep_pattern_ignored_glob_path_used(self):
        paths = tj.extract_tool_paths("Grep", {"pattern": "def foo", "path": "C:/repo/src"})
        assert paths == ["c:/repo/src"]

    def test_missing_fields_empty(self):
        assert tj.extract_tool_paths("WebSearch", {}) == []
        assert tj.extract_tool_paths("Bash", None) == []
