# -*- coding: utf-8 -*-
"""v11 — Session-Start-Projektkarte (Runde 3, docs/2026-09-28-plan-relevanz-replay.md).

Validiert an 46 unberührten Session-Anfängen: Projekt-Partition hebt useful
9 % -> 26 %, noise 26 % -> 20 %. Beim ersten Prompt einer Session (noch keine
Assistant-Antwort im Transkript) sucht der Hook unabhängig vom Work-Signal-Gate
(ab 40 Zeichen, wie getestet) und liefert zusätzlich bis zu 2 Treffer des
aktuellen Projekts. Fortsetzungen verhalten sich wie v10."""
import json as _json

import prompt_prelude as pp

EVOLAB = r"C:\Users\alice\AI\evolab"
# Kein Work-Signal, >= 40 Zeichen: v10 würde no_work_signal skippen.
NWS_PROMPT = "hört sich gut an, aber die farbe passt noch nicht so ganz zum rest"

SEARCH = {"results": [
    {"record_id": "atlas/skill:frontend-design", "snippet": "ui design skill"},
    {"record_id": "agent-memory/evolab/learnings/L13",
     "snippet": "Windows real-Lighthouse Chrome version handling"},
    {"record_id": "docs-evolab/README.md", "snippet": "evolutionaerer Lighthouse-Optimierer"},
    {"record_id": "atlas/project:crazy-professor", "snippet": "ideen"},
]}


def _http(calls=None):
    def fn(url, body, timeout):
        if calls is not None:
            calls.append((url, body.get("query")))
        if url.endswith("/classify"):
            return {"scores": [{"name": "ui-frontend", "score": 0.2},
                               {"name": "meta-none", "score": 0.1}]}
        return SEARCH
    return fn


def _transcript(tmp_path, with_assistant):
    p = tmp_path / "t.jsonl"
    lines = [_json.dumps({"type": "attachment", "content": "session start briefing"}),
             _json.dumps({"type": "user", "message": {"content": "hallo"}}, separators=(",", ":"))]
    if with_assistant:
        lines.append(_json.dumps({"type": "assistant", "message": {"content": []}},
                                 separators=(",", ":")))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def _run(tmp_path, prompt, transcript, cwd=EVOLAB, calls=None):
    log = tmp_path / "l.jsonl"
    out = pp.run({"prompt": prompt, "session_id": "s", "cwd": cwd,
                  "transcript_path": transcript},
                 atlas_root=str(tmp_path / "no-atlas"), state_dir=str(tmp_path / "st"),
                 log_path=str(log), now=1.0, http_fn=_http(calls))
    ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
    return out, ev


class TestIsSessionStart:
    def test_no_assistant_record_yet(self, tmp_path):
        assert pp.is_session_start(_transcript(tmp_path, with_assistant=False)) is True

    def test_assistant_record_present(self, tmp_path):
        assert pp.is_session_start(_transcript(tmp_path, with_assistant=True)) is False

    def test_missing_or_empty_path_is_not_start(self, tmp_path):
        # Unbekannt -> bisheriges Verhalten (kein Gate-Bypass).
        assert pp.is_session_start("") is False
        assert pp.is_session_start(None) is False
        assert pp.is_session_start(str(tmp_path / "gibt-es-nicht.jsonl")) is False

    def test_escaped_marker_inside_text_does_not_count(self, tmp_path):
        p = tmp_path / "t.jsonl"
        p.write_text(_json.dumps({"type": "user", "message": {
            "content": 'paste: {"type":"assistant"}'}}, separators=(",", ":")) + "\n",
            encoding="utf-8")
        assert pp.is_session_start(str(p)) is True

    def test_huge_transcript_without_marker_is_not_start(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pp, "SESSION_START_SCAN_BYTES", 10)
        p = tmp_path / "t.jsonl"
        p.write_text("x" * 50, encoding="utf-8")
        assert pp.is_session_start(str(p)) is False


class TestSessionState:
    def test_three_states(self, tmp_path):
        assert pp.session_state(_transcript(tmp_path, with_assistant=False)) == "start"
        assert pp.session_state(_transcript(tmp_path, with_assistant=True)) == "continuation"
        assert pp.session_state("") == "unknown"
        assert pp.session_state(str(tmp_path / "fehlt.jsonl")) == "unknown"


class TestV12CapsOffOnContinuation:
    """v12 (Owner-Delegation 2026-09-28, NOTES Befund 15): Caps sind die
    schwächste Partition (18 % Präzision, ~1 % Nutzung); ohne sie sinkt bei
    Fortsetzungen noise 24 % -> 4 %. Nur bei NACHWEISLICHER Fortsetzung —
    Session-Start (v11-validiert) und unbekannter Zustand behalten Caps."""

    # Arbeits-Signal nötig: bei Fortsetzungen gibt es keinen Gate-Bypass.
    WORK = "debugge warum lighthouse mit der falschen chrome version startet"

    def test_continuation_drops_caps_keeps_mentors(self, tmp_path):
        out, ev = _run(tmp_path, self.WORK, _transcript(tmp_path, True))
        assert ev["fired"] is True
        assert ev["caps"] == [] and ev["caps_suppressed"] == 2
        assert ev["mentor_count"] == 1
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "VORAB-SUCHE Capability-RAG" not in ctx
        assert "Frühere Fälle" in ctx

    def test_continuation_with_only_caps_is_no_material(self, tmp_path):
        global SEARCH
        saved = SEARCH
        SEARCH = {"results": [r for r in saved["results"]
                              if r["record_id"].startswith("atlas/")]}
        try:
            out, ev = _run(tmp_path, self.WORK, _transcript(tmp_path, True))
        finally:
            SEARCH = saved
        assert ev["skip"] == "no_material" and ev["caps_suppressed"] == 2

    def test_session_start_keeps_caps(self, tmp_path):
        _out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, False))
        assert ev["caps_count"] == 2 and ev["caps_suppressed"] == 0

    def test_unknown_state_keeps_caps(self, tmp_path):
        log = tmp_path / "l.jsonl"
        pp.run({"prompt": self.WORK, "session_id": "s", "cwd": EVOLAB},
               atlas_root=str(tmp_path / "no-atlas"), state_dir=str(tmp_path / "st"),
               log_path=str(log), now=1.0, http_fn=_http())
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["caps_count"] == 2 and ev["caps_suppressed"] == 0


class TestSessionStartRun:
    def test_first_prompt_bypasses_work_signal_gate_and_adds_project_card(self, tmp_path):
        calls = []
        out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, False), calls=calls)
        assert ev["fired"] is True and ev["session_start"] is True
        assert ev["project"] == ["agent-memory/evolab/learnings/L13", "docs-evolab/README.md"]
        assert ev["project_count"] == 2 and ev["project_slugs"] == ["evolab"]
        # zweite Suche mit Projektname in der Query
        searches = [q for url, q in calls if url.endswith("/search")]
        assert len(searches) == 2 and searches[1].endswith(" evolab")
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "PROJEKT-KONTEXT evolab" in ctx
        assert "Windows real-Lighthouse Chrome version handling" in ctx  # Inhalt, nicht nur Verweis
        assert "· projekt=2" in _json.loads(out)["systemMessage"]

    def test_continuation_keeps_v10_behaviour(self, tmp_path):
        calls = []
        out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, True), calls=calls)
        assert ev["skip"] == "no_work_signal" and ev["session_start"] is False
        assert not [u for u, _q in calls if u.endswith("/search")]

    def test_short_first_prompt_without_work_signal_stays_silent(self, tmp_path):
        # Getestet wurde ab 40 Zeichen — darunter kein Bypass.
        prompt = "passt so, die farbe ist gut jetzt"  # 33 Zeichen, kein Work-Signal
        assert len(prompt) < pp.SESSION_START_MIN_LEN
        _out, ev = _run(tmp_path, prompt, _transcript(tmp_path, False))
        assert ev["skip"] == "no_work_signal"

    def test_project_hits_exclude_already_injected(self, tmp_path):
        # L13 als Mentor gewählt (Overlap-Gate: "lighthouse" + "chrome") ->
        # darf nicht noch einmal als Projekt-Treffer auftauchen.
        prompt = "warum startet lighthouse mit der falschen chrome version beim lauf"
        _out, ev = _run(tmp_path, prompt, _transcript(tmp_path, False))
        assert "agent-memory/evolab/learnings/L13" in " ".join(ev["mentor"])
        assert ev["project"] == ["docs-evolab/README.md"]

    def test_no_project_slug_no_second_search(self, tmp_path):
        calls = []
        _out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, False),
                        cwd=r"C:\Users\alice", calls=calls)
        assert len([u for u, _q in calls if u.endswith("/search")]) == 1
        assert ev.get("project_count", 0) == 0

    def test_no_material_event_shows_project_search_outcome(self, tmp_path):
        # E2E-Befund: Projekt-Suche lief, fand aber nichts -> no_material. Die
        # Telemetrie muss "gesucht, nichts gefunden" von "nicht gesucht" trennen.
        global SEARCH
        saved, SEARCH = SEARCH, {"results": []}
        try:
            _out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, False))
        finally:
            SEARCH = saved
        assert ev["skip"] == "no_material" and ev["session_start"] is True
        assert ev["project_slugs"] == ["evolab"] and ev["project_source"] == "none"

    def test_schema_v12(self, tmp_path):
        _out, ev = _run(tmp_path, NWS_PROMPT, _transcript(tmp_path, False))
        assert ev["v"] == 12
