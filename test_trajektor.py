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


def _mk_window(specs):
    """specs: Liste (tool, [paths]) -> Window-Dict."""
    w = tj._default_window()
    for tool, paths in specs:
        w = tj.update_window(w, tool, paths)
    return w


def _mk_anchor(tokens, dirs, phase="quiet"):
    return {"t": 0, "prompt_preview": "x", "domain": "code-impl",
            "phase": phase, "tokens": sorted(tokens), "dirs": sorted(dirs)}


class TestDriftScore:
    def test_on_track_low_score(self):
        anchor = _mk_anchor({"trajektor", "drift", "hook"}, ["c:/repo/prompt-prelude"])
        w = _mk_window([("Edit", ["c:/repo/prompt-prelude/trajektor.py"]),
                        ("Read", ["c:/repo/prompt-prelude/test_trajektor.py"])])
        s = tj.drift_score(anchor, w)
        assert s["total"] < 0.45          # unter TRAJ_CLEAR: eindeutig on-track
        assert s["path_divergence"] == 0.0

    def test_hard_jump_high_score(self):
        anchor = _mk_anchor({"login", "modul", "backend"}, ["c:/repo/auth"])
        w = _mk_window([("Edit", ["c:/other/site/css/theme.css"]),
                        ("Edit", ["c:/other/site/index.html"]),
                        ("Bash", ["c:/other/site/build.sh"])])
        s = tj.drift_score(anchor, w)
        assert s["total"] >= 0.65         # über TRAJ_FIRE

    def test_phase_flip_contributes(self):
        anchor = _mk_anchor({"analyse", "code"}, [], phase="planning")
        w = _mk_window([("Edit", ["c:/r/a.py"]), ("Write", ["c:/r/b.py"]),
                        ("Edit", ["c:/r/a.py"])])
        s = tj.drift_score(anchor, w)
        assert s["window_phase"] == "build"
        assert s["phase_flip"] == 1.0

    def test_empty_window_zero(self):
        s = tj.drift_score(_mk_anchor({"x"}, []), tj._default_window())
        assert s["total"] == 0.0

    def test_no_anchor_dirs_no_divergence(self):
        # Anchor ohne Pfade: path_divergence neutral 0 (nicht 1) — kein Fehlalarm
        anchor = _mk_anchor({"refactor", "tests"}, [])
        w = _mk_window([("Edit", ["c:/anywhere/x.py"])])
        assert tj.drift_score(anchor, w)["path_divergence"] == 0.0


class TestPhaseFromTools:
    def test_explore(self):
        assert tj.phase_from_tools(_mk_window([("Read", []), ("Grep", []), ("Glob", [])])) == "explore"

    def test_build(self):
        assert tj.phase_from_tools(_mk_window([("Edit", []), ("Write", []), ("Edit", [])])) == "build"

    def test_verify(self):
        w = _mk_window([("Bash", ["tests/test_a.py"]), ("Bash", ["pytest"])])
        assert tj.phase_from_tools(w) == "verify"

    def test_mixed(self):
        assert tj.phase_from_tools(_mk_window([("Read", []), ("Edit", [])])) == "mixed"
