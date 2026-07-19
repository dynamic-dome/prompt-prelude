import json
import os
import subprocess
import sys

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

    def test_empty_window_tokens_max_shift(self):
        # Fenster mit Calls, aber ohne signifikante Pfad-Tokens (z.B. WebSearch,
        # Bash ohne Pfad-Argument): token_shift schlägt voll aus (1.0), aber
        # ohne Anchor-Pfad-Urteil bleibt total unter TRAJ_FIRE (kein Fehlalarm).
        anchor = _mk_anchor({"login", "backend"}, [])
        w = _mk_window([("WebSearch", []), ("TodoWrite", []), ("Bash", [])])
        s = tj.drift_score(anchor, w)
        assert s["token_shift"] == 1.0
        assert s["path_divergence"] == 0.0
        assert s["total"] == 0.5  # 0.5*1.0 — unterhalb TRAJ_FIRE (0.65)


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


class TestDecide:
    def _w(self, call_count=20, armed=True, cooldown_until=0, fires=0):
        return {**tj._default_window(), "call_count": call_count,
                "armed": armed, "cooldown_until": cooldown_until, "fires": fires}

    @pytest.mark.parametrize("total,armed,cooldown_until,fires,expected", [
        (0.70, True, 0, 0, "fire"),      # klarer Fire
        (0.50, True, 0, 0, "below"),     # zwischen clear und fire: nichts
        (0.70, False, 0, 0, "not_armed"),# über fire, aber Hysterese offen
        (0.70, True, 25, 0, "cooldown"), # Cooldown aktiv (call_count 20 < 25)
        (0.70, True, 0, 3, "cap"),       # Session-Cap erreicht
    ])
    def test_table(self, total, armed, cooldown_until, fires, expected):
        status, _ = tj.decide(self._w(armed=armed, cooldown_until=cooldown_until,
                                      fires=fires), total)
        assert status == expected

    def test_fire_sets_cooldown_and_disarms(self):
        status, w = tj.decide(self._w(call_count=20), 0.9)
        assert status == "fire"
        assert w["armed"] is False and w["cooldown_until"] == 30 and w["fires"] == 1

    def test_rearm_below_clear(self):
        status, w = tj.decide(self._w(armed=False), 0.30)   # unter TRAJ_CLEAR
        assert status == "below" and w["armed"] is True

    def test_no_rearm_between_clear_and_fire(self):
        # 0.50 liegt zwischen clear (0.45) und fire (0.65): Status "below"
        # (kein Fire-Kandidat), aber KEIN Re-Arm — armed bleibt False.
        status, w = tj.decide(self._w(armed=False), 0.50)
        assert status == "below" and w["armed"] is False

    def test_flapping_score_fires_once(self):
        # 0.7 -> 0.6 -> 0.7: ohne Absinken unter clear kein zweites Fire
        w = self._w(call_count=0, cooldown_until=-1)
        w["call_count"] = 50  # jenseits jedes Cooldowns
        s1, w = tj.decide(w, 0.7)
        w["call_count"] += 20
        s2, w = tj.decide(w, 0.6)   # unter fire -> below, kein Re-Arm (0.6 > clear)
        w["call_count"] += 20
        s3, w = tj.decide(w, 0.7)   # über fire, aber nicht armed -> not_armed
        assert (s1, s2, s3) == ("fire", "below", "not_armed")

    def test_boundary_exact_fire_threshold(self):
        # total == TRAJ_FIRE (0.65) zählt als Fire-Kandidat (decide nutzt total < TRAJ_FIRE für below)
        status, w = tj.decide(self._w(call_count=20), tj.TRAJ_FIRE)
        assert status == "fire"

    def test_boundary_exact_clear_threshold(self):
        # total == TRAJ_CLEAR (0.45) re-armt (decide nutzt total <= TRAJ_CLEAR)
        status, w = tj.decide(self._w(armed=False), tj.TRAJ_CLEAR)
        assert status == "below" and w["armed"] is True


class TestOutputAndRun:
    def test_make_ptu_output_schema(self):
        out = json.loads(tj.make_ptu_output("CTX", "MSG"))
        assert out["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
        assert out["hookSpecificOutput"]["additionalContext"] == "CTX"
        assert out["systemMessage"] == "MSG"

    def test_make_ptu_output_empty(self):
        assert tj.make_ptu_output(None, None) == ""

    def test_build_reframing_contains_anchor_and_verdict(self):
        anchor = _mk_anchor({"login", "backend"}, ["c:/repo/auth"])
        anchor["prompt_preview"] = "Implementiere das Login-Modul"
        s = {"total": 0.8, "token_shift": 0.9, "path_divergence": 0.7,
             "phase_flip": 0.0, "window_phase": "build"}
        text = tj.build_reframing(anchor, s)
        assert "Implementiere das Login-Modul" in text
        assert "0.8" in text

    def _payload(self, sid, tool="Edit", path="c:/other/x.py"):
        return {"session_id": sid, "tool_name": tool,
                "tool_input": {"file_path": path}}

    def test_run_traj_no_anchor_silent(self, tmp_path):
        out = tj.run_traj(self._payload("s-noanchor"), state_dir=str(tmp_path),
                          log_path=str(tmp_path / "t.jsonl"), now=1.0)
        assert out == ""
        rec = json.loads((tmp_path / "t.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert rec["status"] == "no_anchor" and rec["tv"] == "t1"

    def test_run_traj_fires_on_hard_drift(self, tmp_path):
        import prompt_prelude as pp
        anchor = pp.build_anchor("Implementiere das Login-Modul in c:/repo/auth/mod.py",
                                 "code-impl", "quiet", now=1.0)
        pp.save_anchor("s-drift", str(tmp_path), anchor)
        out = ""
        for i in range(6):  # 6 fremde Edits -> Score über TRAJ_FIRE
            out = tj.run_traj(self._payload("s-drift", path=f"c:/webshop/theme/part{i}.css"),
                              state_dir=str(tmp_path),
                              log_path=str(tmp_path / "t.jsonl"), now=float(i))
            if out:
                break
        parsed = json.loads(out)
        assert "Login-Modul" in parsed["hookSpecificOutput"]["additionalContext"]
        assert parsed["systemMessage"].startswith("trajektor ▸ drift")

    def test_run_traj_on_track_never_fires(self, tmp_path):
        import prompt_prelude as pp
        anchor = pp.build_anchor("Baue trajektor.py in c:/repo/prompt-prelude weiter",
                                 "code-impl", "quiet", now=1.0)
        pp.save_anchor("s-ok", str(tmp_path), anchor)
        for i in range(20):
            out = tj.run_traj(self._payload("s-ok", path="c:/repo/prompt-prelude/trajektor.py"),
                              state_dir=str(tmp_path),
                              log_path=str(tmp_path / "t.jsonl"), now=float(i))
            assert out == ""   # eigener Test: on-track feuert NIE


class TestE2E:
    SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trajektor.py")

    def _run(self, payload):
        return subprocess.run([sys.executable, self.SCRIPT],
                              input=json.dumps(payload).encode("utf-8"),
                              capture_output=True, timeout=10)

    def test_e2e_exit_zero_and_silent(self):
        r = self._run({"session_id": "e2e-tj", "tool_name": "Read",
                       "tool_input": {"file_path": "C:/x/y.py"}})
        assert r.returncode == 0
        assert r.stdout.strip() == b""   # kein Anchor -> still

    def test_e2e_garbage_stdin_exit_zero(self):
        r = subprocess.run([sys.executable, self.SCRIPT], input=b"{kaputt",
                           capture_output=True, timeout=10)
        assert r.returncode == 0 and r.stdout.strip() == b""


class TestDeterminism:
    def test_same_stream_same_log(self, tmp_path):
        import prompt_prelude as pp
        stream = [{"session_id": "det", "tool_name": "Edit",
                   "tool_input": {"file_path": f"c:/other/f{i}.css"}} for i in range(8)]
        logs = []
        for run_dir in ("a", "b"):
            d = tmp_path / run_dir
            d.mkdir()
            pp.save_anchor("det", str(d), pp.build_anchor(
                "Baue Login in c:/repo/auth", "code-impl", "quiet", now=1.0))
            log = d / "t.jsonl"
            for i, p in enumerate(stream):
                tj.run_traj(p, state_dir=str(d), log_path=str(log), now=float(i))
            # ts raus, Rest muss byte-identisch sein
            recs = [json.loads(l) for l in log.read_text(encoding="utf-8").splitlines()]
            for r in recs:
                r.pop("t", None)
            logs.append(json.dumps(recs, sort_keys=True))
        assert logs[0] == logs[1]


class TestOverhead:
    def test_median_under_budget(self, tmp_path):
        import statistics
        import time as _time
        import prompt_prelude as pp
        pp.save_anchor("perf", str(tmp_path), pp.build_anchor(
            "Perf-Anchor c:/repo/x", "code-impl", "quiet", now=1.0))
        times = []
        for i in range(20):
            t0 = _time.perf_counter()
            tj.run_traj({"session_id": "perf", "tool_name": "Read",
                         "tool_input": {"file_path": f"c:/repo/x/{i}.py"}},
                        state_dir=str(tmp_path), log_path=str(tmp_path / "t.jsonl"),
                        now=float(i))
            times.append((_time.perf_counter() - t0) * 1000)
        # In-Process-Hot-Path; der Python-Start (~50-100ms) kommt real dazu.
        # Spec-Budget 150ms gesamt -> Hot-Path muss deutlich darunter liegen.
        assert statistics.median(times) < 50, f"Median {statistics.median(times):.1f}ms"
