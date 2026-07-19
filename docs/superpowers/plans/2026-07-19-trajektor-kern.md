# Trajektor-Kern Implementation Plan (T-12)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** PostToolUse-Hook `trajektor.py`, der Drift gegen den letzten Arbeits-Prompt deterministisch misst und bei bestätigter Drift eine Reframing-Zeile injiziert (non-blocking, fail-soft).

**Architecture:** Schwester-Modul im selben Repo. `prompt_prelude.py` (UserPromptSubmit) schreibt beim Präzisions-Gate-Pass einen Goal-Anchor; `trajektor.py` (PostToolUse) pflegt ein rollendes 15-Call-Fenster, berechnet einen 3-Komponenten-Drift-Score mit Hysterese + Cooldown + Session-Cap und emittiert bei Fire `additionalContext` + `systemMessage`.

**Tech Stack:** Python 3.12 stdlib-only, pytest. Spec: `docs/superpowers/specs/2026-07-19-trajektor-kern-design.md`.

## Global Constraints

- stdlib-only, kein pip-Paket.
- Fail-soft total: jede Exception → Exit 0, kein Output; Advisory-Kanal blockiert NIE.
- Bestehende Suite (187 Tests) bleibt grün; neue Tests in `test_trajektor.py` bzw. `test_prompt_prelude.py`.
- stdin IMMER als UTF-8-Bytes lesen (v4-Härtung, `_read_stdin_utf8`).
- State-Dateien atomar schreiben (tmp + `os.replace`), `_safe_session`-Härtung.
- Telemetrie `trajektor.jsonl`, Schema-Feld `"tv": "t1"` — nie mit prelude-Ären mischen.
- Schwellen: `TRAJ_FIRE = 0.65`, `TRAJ_CLEAR = 0.45`, `COOLDOWN_CALLS = 10`, `SESSION_FIRE_CAP = 3`, `WINDOW_K = 15`.
- Score-Gewichte: `W_TOKEN = 0.5`, `W_PATH = 0.3`, `W_PHASE = 0.2`.

---

### Task 1: Goal-Anchor-Schreiber in prompt_prelude.py

**Files:**
- Modify: `prompt_prelude.py` (neue Funktionen nach `save_fired`, Zeile ~359; Aufruf in `run()` nach dem Confidence-Gate, Zeile ~894)
- Test: `test_prompt_prelude.py` (neue Testklasse am Ende)

**Interfaces:**
- Consumes: `STOP_WORDS`, `_safe_session`, `topic_signature`-Tokenregex-Muster (bestehend)
- Produces: `significant_tokens(text) -> set[str]` · `extract_prompt_paths(prompt) -> list[str]` · `anchor_path(session_id, state_dir) -> str` (Datei `anchor_<sid>.json`) · `save_anchor(session_id, state_dir, anchor: dict)` · `build_anchor(prompt, domain, phase, now) -> dict` mit Keys `{"t", "prompt_preview", "domain", "phase", "tokens": sorted list, "dirs": sorted list}` — Task 2–5 lesen dieses Schema.

- [ ] **Step 1: Failing Tests schreiben** (ans Ende von `test_prompt_prelude.py`)

```python
class TestGoalAnchor:
    def test_build_anchor_schema(self):
        a = pp.build_anchor("Baue den Trajektor in trajektor.py fertig",
                            "code-impl", "planning", now=1000.0)
        assert a["domain"] == "code-impl" and a["phase"] == "planning"
        assert a["t"] == 1000.0
        assert "trajektor" in a["tokens"]          # signifikantes Token
        assert "baue" in a["tokens"]
        assert a["prompt_preview"].startswith("Baue den Trajektor")

    def test_build_anchor_extracts_dirs(self):
        a = pp.build_anchor(r"Fix in C:\Users\domes\AI\Hooks-bau\prompt-prelude\prompt_prelude.py bitte",
                            "debug", "quiet", now=1.0)
        assert any("prompt-prelude" in d for d in a["dirs"])

    def test_save_and_load_anchor_roundtrip(self, tmp_path):
        trajektor = pytest.importorskip("trajektor")  # existiert erst ab Task 2
        a = pp.build_anchor("Refactor XY", "code-impl", "quiet", now=1.0)
        pp.save_anchor("sess1", str(tmp_path), a)
        loaded = trajektor.load_anchor("sess1", str(tmp_path))
        assert loaded == a

    def test_save_anchor_fail_soft(self):
        pp.save_anchor("s", "Z:\\nonexistent\\dir\\x", {"t": 1})  # darf nicht werfen

    def test_run_writes_anchor_on_gate_pass(self, tmp_path):
        payload = {"prompt": "Implementiere bitte das neue Login-Modul mit Tests",
                   "session_id": "anchor-sess"}
        pp.run(payload, atlas_root=str(tmp_path / "no-atlas"),
               state_dir=str(tmp_path), log_path=str(tmp_path / "t.jsonl"),
               now=1000.0, http_fn=lambda *a, **k: None)
        assert os.path.exists(pp.anchor_path("anchor-sess", str(tmp_path)))

    def test_run_no_anchor_on_skip(self, tmp_path):
        payload = {"prompt": "ok", "session_id": "skip-sess"}  # trivial -> skip
        pp.run(payload, atlas_root=str(tmp_path / "no-atlas"),
               state_dir=str(tmp_path), log_path=str(tmp_path / "t.jsonl"),
               now=1000.0, http_fn=lambda *a, **k: None)
        assert not os.path.exists(pp.anchor_path("skip-sess", str(tmp_path)))
```

- [ ] **Step 2: Fail verifizieren**

Run: `python -m pytest test_prompt_prelude.py::TestGoalAnchor -q`
Expected: FAIL, `AttributeError: ... has no attribute 'build_anchor'`

- [ ] **Step 3: Implementierung** (in `prompt_prelude.py` nach `save_fired`)

```python
TOKEN_RE = re.compile(r"[a-zA-ZäöüÄÖÜß]{4,}")
# Pfadartige Strings im Prompt: Windows- (C:\..., .\foo\bar) oder POSIX-artig (a/b/c.py)
PROMPT_PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\.{0,2}[\\/])?(?:[\w.-]+[\\/])+[\w.-]+")


def significant_tokens(text):
    """Signifikante Tokens (>=4 Zeichen, ohne Stopwörter) — geteilt mit Trajektor."""
    if not isinstance(text, str):
        return set()
    return set(TOKEN_RE.findall(text.lower())) - STOP_WORDS


def extract_prompt_paths(prompt):
    """Pfadartige Strings aus dem Prompt; zurück kommen die Verzeichnis-Anteile."""
    dirs = set()
    for m in PROMPT_PATH_RE.findall(prompt or ""):
        norm = m.replace("\\", "/").rstrip("/")
        parent = norm.rsplit("/", 1)[0] if "/" in norm else norm
        if parent:
            dirs.add(parent.lower())
    return sorted(dirs)


def build_anchor(prompt, domain, phase, now):
    return {
        "t": now,
        "prompt_preview": str(prompt).strip()[:120],
        "domain": domain,
        "phase": phase,
        "tokens": sorted(significant_tokens(prompt)),
        "dirs": extract_prompt_paths(prompt),
    }


def anchor_path(session_id, state_dir):
    return os.path.join(state_dir, f"anchor_{_safe_session(session_id)}.json")


def save_anchor(session_id, state_dir, anchor):
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = anchor_path(session_id, state_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(anchor, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass
```

In `run()` direkt NACH dem `low_domain_confidence`-Block (nach Zeile ~893, vor `if not routing:`) einfügen:

```python
    # T-12 Goal-Anchor: jeder Prompt, der das Präzisions-Gate passiert, re-anchort
    # den Trajektor (Anchor-Politik: letzter Arbeits-Prompt; Kurz-Zurufe skippen oben).
    save_anchor(session_id, state_dir, build_anchor(prompt, domain, phase, now))
```

`load_anchor` lebt in `trajektor.py` (Task 2) — der Roundtrip-Test erzwingt Schema-Kompatibilität beider Module.

- [ ] **Step 4: Tests grün + Alt-Suite grün**

Run: `python -m pytest test_prompt_prelude.py -q` — Expected: alle PASS (Roundtrip-Test schlägt noch fehl, solange Task 2 fehlt → diesen einen Test mit `pytest.importorskip("trajektor")` beginnen lassen: `trajektor = pytest.importorskip("trajektor")`).

- [ ] **Step 5: Commit**

```bash
git add prompt_prelude.py test_prompt_prelude.py
git commit -m "Feat(T-12): Goal-Anchor-Schreiber — Gate-Pass persistiert letzten Arbeits-Prompt"
```

---

### Task 2: trajektor.py — Fenster-State + Pfad-Extraktion + stilles main()

**Files:**
- Create: `trajektor.py`
- Create: `test_trajektor.py`

**Interfaces:**
- Consumes: `prompt_prelude._safe_session`, `prompt_prelude._read_stdin_utf8`, `prompt_prelude.significant_tokens`, Anchor-Schema aus Task 1
- Produces: `load_anchor(session_id, state_dir) -> dict|None` · `window_path(session_id, state_dir) -> str` (Datei `trajwin_<sid>.json`) · `load_window(session_id, state_dir) -> dict` · `save_window(session_id, state_dir, window)` · `extract_tool_paths(tool_name, tool_input) -> list[str]` · `update_window(window, tool_name, paths) -> dict`. Window-Schema: `{"calls": [{"tool": str, "paths": [str]}], "call_count": int, "armed": bool, "cooldown_until": int, "fires": int}` — Task 3–5 bauen darauf.

- [ ] **Step 1: Failing Tests** (`test_trajektor.py` anlegen)

```python
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
```

- [ ] **Step 2: Fail verifizieren** — Run: `python -m pytest test_trajektor.py -q` — Expected: FAIL `ModuleNotFoundError: trajektor`

- [ ] **Step 3: Implementierung** (`trajektor.py` anlegen)

```python
"""trajektor.py — PostToolUse-Drift-Beobachter (T-12, Spec 2026-07-19).

Schwester-Hook von prompt_prelude: misst Drift des Tool-Call-Stroms gegen den
letzten Arbeits-Prompt (Goal-Anchor) deterministisch — keine Daemon-Calls,
fail-soft, Exit immer 0."""
import json
import os
import re
import sys
import time

from prompt_prelude import (_read_stdin_utf8, _safe_session, cleanup_state,
                            significant_tokens)

WINDOW_K = 15


def load_anchor(session_id, state_dir):
    try:
        from prompt_prelude import anchor_path
        with open(anchor_path(session_id, state_dir), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def window_path(session_id, state_dir):
    return os.path.join(state_dir, f"trajwin_{_safe_session(session_id)}.json")


def _default_window():
    return {"calls": [], "call_count": 0, "armed": True, "cooldown_until": 0, "fires": 0}


def load_window(session_id, state_dir):
    try:
        with open(window_path(session_id, state_dir), encoding="utf-8") as f:
            w = json.load(f)
        if not isinstance(w, dict) or not isinstance(w.get("calls"), list):
            return _default_window()
        for key, default in _default_window().items():
            w.setdefault(key, default)
        return w
    except Exception:
        return _default_window()


def save_window(session_id, state_dir, window):
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = window_path(session_id, state_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(window, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


# Pfadartig innerhalb von Bash-Kommandos: mindestens ein Separator
_BASH_PATH_RE = re.compile(r"[\w.~-]+(?:[\\/][\w.~-]+)+")


def _norm(p):
    return str(p).replace("\\", "/").lower()


def extract_tool_paths(tool_name, tool_input):
    """Berührte Pfade aus tool_input — fail-soft, fehlende Felder egal."""
    if not isinstance(tool_input, dict):
        return []
    paths = []
    for field in ("file_path", "path", "notebook_path"):
        v = tool_input.get(field)
        if isinstance(v, str) and v.strip():
            paths.append(_norm(v.strip()))
    cmd = tool_input.get("command")
    if isinstance(cmd, str):
        paths.extend(_norm(m) for m in _BASH_PATH_RE.findall(cmd))
    # Dedupe bei stabiler Reihenfolge
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def update_window(window, tool_name, paths):
    calls = window["calls"] + [{"tool": tool_name, "paths": paths}]
    return {**window, "calls": calls[-WINDOW_K:],
            "call_count": window["call_count"] + 1}


def main():
    try:
        raw = _read_stdin_utf8()
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            return 0
        if not isinstance(payload, dict):
            return 0
        session_id = payload.get("session_id", "default") or "default"
        state_dir = _default_state_dir()
        cleanup_state(state_dir, time.time())
        window = load_window(session_id, state_dir)
        paths = extract_tool_paths(payload.get("tool_name", ""),
                                   payload.get("tool_input"))
        window = update_window(window, str(payload.get("tool_name", "")), paths)
        save_window(session_id, state_dir, window)
        # Score/Fire folgt in Task 5 — bis dahin stiller Beobachter.
    except Exception:
        pass
    return 0


def _default_state_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Tests grün** — Run: `python -m pytest test_trajektor.py test_prompt_prelude.py -q` — Expected: alle PASS (jetzt auch der Roundtrip-Test aus Task 1)

- [ ] **Step 5: Commit**

```bash
git add trajektor.py test_trajektor.py
git commit -m "Feat(T-12): Trajektor-Skelett — Fenster-State, Pfad-Extraktion, stilles main()"
```

---

### Task 3: Drift-Score (token_shift, path_divergence, phase_flip)

**Files:**
- Modify: `trajektor.py`
- Test: `test_trajektor.py`

**Interfaces:**
- Consumes: Window-/Anchor-Schema, `significant_tokens`
- Produces: `window_tokens(window) -> set[str]` · `phase_from_tools(window) -> str` (`"explore"|"build"|"verify"|"mixed"`) · `drift_score(anchor, window) -> dict` mit Keys `{"total": float, "token_shift": float, "path_divergence": float, "phase_flip": float, "window_phase": str}`; `total = 0.5*token_shift + 0.3*path_divergence + 0.2*phase_flip`, alle Komponenten in [0,1].

- [ ] **Step 1: Failing Tests**

```python
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
```

- [ ] **Step 2: Fail verifizieren** — Run: `python -m pytest test_trajektor.py -q` — Expected: neue Tests FAIL (`drift_score` fehlt)

- [ ] **Step 3: Implementierung** (in `trajektor.py`)

```python
W_TOKEN, W_PATH, W_PHASE = 0.5, 0.3, 0.2

_EXPLORE_TOOLS = {"Read", "Grep", "Glob"}
_BUILD_TOOLS = {"Edit", "Write", "NotebookEdit"}
_TEST_MARKERS = ("pytest", "test", "npm run", "cargo test", "go test")


def window_tokens(window):
    toks = set()
    for call in window["calls"]:
        for p in call["paths"]:
            toks |= significant_tokens(p.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " "))
    return toks


def phase_from_tools(window):
    tools = [c["tool"] for c in window["calls"]]
    if not tools:
        return "mixed"
    n = len(tools)
    explore = sum(1 for t in tools if t in _EXPLORE_TOOLS)
    build = sum(1 for t in tools if t in _BUILD_TOOLS)
    verify = sum(1 for c in window["calls"] if c["tool"] == "Bash"
                 and any(m in " ".join(c["paths"]) for m in _TEST_MARKERS))
    if verify >= max(1, n // 2):
        return "verify"
    if build > n * 0.6:
        return "build"
    if explore > n * 0.6:
        return "explore"
    return "mixed"


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def drift_score(anchor, window):
    """Deterministischer 3-Komponenten-Drift-Score in [0,1]."""
    if not window["calls"]:
        return {"total": 0.0, "token_shift": 0.0, "path_divergence": 0.0,
                "phase_flip": 0.0, "window_phase": "mixed"}
    a_toks = set(anchor.get("tokens") or [])
    w_toks = window_tokens(window)
    token_shift = 1.0 - _jaccard(a_toks, w_toks) if a_toks and w_toks else (1.0 if a_toks else 0.0)

    a_dirs = [d for d in (anchor.get("dirs") or [])]
    all_paths = [p for c in window["calls"] for p in c["paths"]]
    if a_dirs and all_paths:
        outside = sum(1 for p in all_paths if not any(d in p for d in a_dirs))
        path_divergence = outside / len(all_paths)
    else:
        path_divergence = 0.0  # ohne Anchor-Pfade kein Urteil (neutral, kein Fehlalarm)

    window_phase = phase_from_tools(window)
    anchor_phase = anchor.get("phase") or "quiet"
    # planning-Anchor + reine build-Kette = klarer Phasenwechsel; quiet urteilt nicht
    phase_flip = 1.0 if (anchor_phase == "planning" and window_phase == "build") else 0.0

    total = W_TOKEN * token_shift + W_PATH * path_divergence + W_PHASE * phase_flip
    return {"total": round(total, 4), "token_shift": round(token_shift, 4),
            "path_divergence": round(path_divergence, 4),
            "phase_flip": phase_flip, "window_phase": window_phase}
```

- [ ] **Step 4: Tests grün** — Run: `python -m pytest test_trajektor.py -q` — Expected: PASS. Falls `test_on_track_low_score` an `token_shift` scheitert (Jaccard naturgemäß < 1 Overlap): Anchor-Tokens im Test decken die Pfadsegmente ab — Gewichte NICHT verändern, sondern Fixture prüfen; die Schwellen-Semantik ist Spec-fixiert.

- [ ] **Step 5: Commit**

```bash
git add trajektor.py test_trajektor.py
git commit -m "Feat(T-12): Drift-Score — token_shift/path_divergence/phase_flip, deterministisch"
```

---

### Task 4: Hysterese, Cooldown, Session-Cap — decide()

**Files:**
- Modify: `trajektor.py`
- Test: `test_trajektor.py`

**Interfaces:**
- Consumes: Window-Schema (`armed`, `cooldown_until`, `fires`, `call_count`), `drift_score`-total
- Produces: `TRAJ_FIRE = 0.65`, `TRAJ_CLEAR = 0.45`, `COOLDOWN_CALLS = 10`, `SESSION_FIRE_CAP = 3` · `decide(window, total) -> tuple[str, dict]` — Status ∈ `{"fire", "below", "not_armed", "cooldown", "cap"}`, zweites Element ist das aktualisierte Window (bei `"fire"`: `armed=False`, `cooldown_until=call_count+10`, `fires+1`; bei `"below"` mit `total <= TRAJ_CLEAR`: re-arm).

- [ ] **Step 1: Failing Tests** (tabellengetrieben)

```python
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
```

- [ ] **Step 2: Fail verifizieren** — Run: `python -m pytest test_trajektor.py::TestDecide -q` — Expected: FAIL (`decide` fehlt)

- [ ] **Step 3: Implementierung**

```python
TRAJ_FIRE = 0.65
TRAJ_CLEAR = 0.45
COOLDOWN_CALLS = 10
SESSION_FIRE_CAP = 3


def decide(window, total):
    """Hysterese + Cooldown + Session-Cap. Gibt (status, updated_window) zurück.

    Reihenfolge der Gates ist Absicht: cap vor cooldown vor hysterese —
    Telemetrie soll den bindendsten Grund nennen."""
    w = dict(window)
    if total <= TRAJ_CLEAR and not w["armed"]:
        w["armed"] = True          # Score beruhigt -> wieder scharf
    if total < TRAJ_FIRE:
        return "below", w
    if w["fires"] >= SESSION_FIRE_CAP:
        return "cap", w
    if w["call_count"] < w["cooldown_until"]:
        return "cooldown", w
    if not w["armed"]:
        return "not_armed", w
    w["armed"] = False
    w["cooldown_until"] = w["call_count"] + COOLDOWN_CALLS
    w["fires"] += 1
    return "fire", w
```

- [ ] **Step 4: Tests grün** — Run: `python -m pytest test_trajektor.py -q` — Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add trajektor.py test_trajektor.py
git commit -m "Feat(T-12): decide() — Hysterese fire/clear, Cooldown 10 Calls, Session-Cap 3"
```

---

### Task 5: Reframing-Output, Telemetrie t1, main()-Verdrahtung

**Files:**
- Modify: `trajektor.py`
- Test: `test_trajektor.py`

**Interfaces:**
- Consumes: alles aus Task 2–4; `prompt_prelude.log_telemetry`-Muster (eigene Kopie mit `tv`-Feld)
- Produces: `build_reframing(anchor, score) -> str` · `make_ptu_output(additional_context, system_message) -> str` (JSON, `hookEventName: "PostToolUse"`) · `log_traj(record, log_path)` (setzt `"tv": "t1"`) · `run_traj(payload, *, state_dir, log_path, now) -> str` · `_default_traj_log()` → `trajektor.jsonl` neben dem Skript. `main()` druckt `run_traj`-Output.

- [ ] **Step 1: Failing Tests**

```python
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
```

- [ ] **Step 2: Fail verifizieren** — Run: `python -m pytest test_trajektor.py::TestOutputAndRun -q` — Expected: FAIL

- [ ] **Step 3: Implementierung**

```python
TRAJ_SCHEMA = "t1"  # Ära t1 — nie mit prompt_prelude.jsonl-Ären mischen (D7)


def log_traj(record, log_path):
    try:
        record.setdefault("tv", TRAJ_SCHEMA)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def build_reframing(anchor, score):
    verdict = "Drift" if score["total"] >= TRAJ_FIRE else "Nebenpfad"
    lines = [
        f"TRAJEKTOR (Drift-Check, deterministisch): Ursprungs-Auftrag war: "
        f"„{anchor.get('prompt_preview', '?')}“",
        f"Der aktuelle Tool-Pfad (Phase {score['window_phase']}) bewertet sich "
        f"dagegen als {verdict} (Score {score['total']}, "
        f"token_shift {score['token_shift']}, path_divergence {score['path_divergence']}).",
        "Kurz prüfen: dient die aktuelle Arbeit noch diesem Auftrag, oder ist es "
        "ein unbeauftragter Nebenpfad? Wenn Nebenpfad: benennen oder zurückkehren.",
    ]
    return "\n".join(lines)


def make_ptu_output(additional_context, system_message):
    if not additional_context and not system_message:
        return ""
    out = {}
    if additional_context:
        out["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    if system_message:
        out["systemMessage"] = system_message
    return json.dumps(out, ensure_ascii=False)


def _default_traj_log():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "trajektor.jsonl")


def run_traj(payload, *, state_dir, log_path, now):
    if not isinstance(payload, dict):
        return ""
    session_id = payload.get("session_id", "default") or "default"
    tool_name = str(payload.get("tool_name", ""))
    window = load_window(session_id, state_dir)
    paths = extract_tool_paths(tool_name, payload.get("tool_input"))
    window = update_window(window, tool_name, paths)

    anchor = load_anchor(session_id, state_dir)
    if not anchor:
        save_window(session_id, state_dir, window)
        log_traj({"t": now, "session": session_id, "status": "no_anchor",
                  "tool": tool_name}, log_path)
        return ""

    score = drift_score(anchor, window)
    status, window = decide(window, score["total"])
    save_window(session_id, state_dir, window)
    rec = {"t": now, "session": session_id, "status": status, "tool": tool_name,
           "score": score, "call_count": window["call_count"],
           "fires": window["fires"]}
    log_traj(rec, log_path)
    if status != "fire":
        return ""
    ctx = build_reframing(anchor, score)
    msg = (f"trajektor ▸ drift · score={score['total']} · "
           f"fire {window['fires']}/{SESSION_FIRE_CAP}")
    return make_ptu_output(ctx, msg)
```

`main()` ersetzen (der stille Beobachter aus Task 2 wird verdrahtet):

```python
def main():
    try:
        raw = _read_stdin_utf8()
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            return 0
        state_dir = _default_state_dir()
        cleanup_state(state_dir, time.time())
        try:
            out = run_traj(payload, state_dir=state_dir,
                           log_path=_default_traj_log(), now=time.time())
            if out:
                print(out)
        except Exception as e:
            log_traj({"t": time.time(), "status": "crash",
                      "error": str(e)[:200]}, _default_traj_log())
    except Exception:
        pass
    return 0
```

- [ ] **Step 4: Alles grün** — Run: `python -m pytest test_trajektor.py test_prompt_prelude.py -q` — Expected: PASS, 187 Alt-Tests unversehrt

- [ ] **Step 5: Commit**

```bash
git add trajektor.py test_trajektor.py
git commit -m "Feat(T-12): Reframing-Injektion + Telemetrie t1 + main()-Verdrahtung"
```

---

### Task 6: E2E, Determinismus, Overhead, Registrierung, README

**Files:**
- Modify: `test_trajektor.py`
- Modify: `README.md` (neuer Abschnitt nach dem prompt-prelude-Setup)
- Modify: `C:\Users\domes\.claude\settings.json` (PostToolUse-Eintrag; NUR diesen Key anfassen)

**Interfaces:**
- Consumes: `trajektor.py` komplett (Task 2–5)
- Produces: Registrierter Hook + dokumentierte Ära t1

- [ ] **Step 1: E2E- und Determinismus-Tests**

```python
import subprocess
import sys


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
```

- [ ] **Step 2: Tests grün** — Run: `python -m pytest test_trajektor.py -q` — Expected: PASS

- [ ] **Step 3: Registrierung** — In `C:\Users\domes\.claude\settings.json` unter `hooks` (bestehende Einträge NICHT anfassen, chirurgisch nur den `PostToolUse`-Array-Eintrag ergänzen):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "python C:/Users/domes/AI/Hooks-bau/prompt-prelude/trajektor.py",
            "timeout": 2
          }
        ]
      }
    ]
  }
}
```

Live-Smoke danach: in einer neuen Session 2–3 Tools laufen lassen, dann prüfen: `trajektor.jsonl` existiert und wächst, Status überwiegend `below`/`no_anchor`, kein Fire bei fokussierter Arbeit.

- [ ] **Step 4: README-Abschnitt** (nach dem bestehenden Setup-Abschnitt einfügen)

```markdown
## Trajektor (PostToolUse-Schwester, T-12)

`trajektor.py` beobachtet den Tool-Call-Strom und misst Drift gegen den letzten
Arbeits-Prompt (Goal-Anchor, geschrieben von prompt_prelude beim Gate-Pass).
Deterministischer 3-Komponenten-Score (token_shift 0.5 / path_divergence 0.3 /
phase_flip 0.2), Hysterese fire=0.65/clear=0.45, Cooldown 10 Calls, max. 3
Fires/Session. Bei Fire: Reframing-Zeile als additionalContext + sichtbare
systemMessage. Fail-soft, Exit immer 0, keine Daemon-Calls.

Telemetrie: `trajektor.jsonl`, Ära **t1** (`tv`-Feld) — nie mit
`prompt_prelude.jsonl`-Ären mischen. Kalibrierung der Schwellen ist bewusst
nachgelagert (Telemetrie-Auswertung analog T-11).
```

- [ ] **Step 5: Voller Suite-Lauf + Commit**

Run: `python -m pytest -q` — Expected: alle Tests grün (187 alt + ~35 neu).

```bash
git add test_trajektor.py README.md
git commit -m "Test(T-12): E2E, Determinismus, Overhead-Budget; README + Registrierung dokumentiert"
```
