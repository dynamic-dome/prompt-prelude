import json as _json

import prompt_prelude as pp


class TestShouldSkip:
    def test_raw_prefix_skips(self):
        assert pp.should_skip("//raw mach genau das") == (True, "raw")

    def test_too_short_skips(self):
        assert pp.should_skip("kurz") == (True, "too_short")

    def test_trivial_word_skips(self):
        assert pp.should_skip("ja bitte") == (True, "trivial")

    def test_under_four_words_skips(self):
        # Kein Füllwort, aber < 4 Wörter -> too_short (ehrlichere Klassifikation als trivial)
        assert pp.should_skip("bitte das machen") == (True, "too_short")

    def test_real_prompt_passes(self):
        skip, reason = pp.should_skip("Baue mir eine Tabelle aus den Verkaufsdaten als Chart")
        assert skip is False and reason == ""

    # --- Härtung nach Codex-Review Task 1 ---
    def test_none_input_does_not_throw(self):
        assert pp.should_skip(None) == (True, "too_short")

    def test_nonstring_input_does_not_throw(self):
        assert pp.should_skip({"a": 1}) == (True, "too_short")

    def test_whitespace_only(self):
        assert pp.should_skip("        ") == (True, "too_short")

    def test_raw_case_insensitive(self):
        assert pp.should_skip("//RAW genau das jetzt bitte tun") == (True, "raw")

    def test_raw_with_leading_space(self):
        assert pp.should_skip("   //raw genau das jetzt bitte tun") == (True, "raw")


class TestDetectDomain:
    def test_ui_prompt(self):
        assert pp.detect_domain("mach das component layout responsive") == "ui-frontend"

    def test_data_prompt(self):
        assert pp.detect_domain("ich will die csv daten auswerten") == "data-analysis"

    def test_debug_prompt(self):
        assert pp.detect_domain("da ist ein bug im traceback") == "debug"

    def test_no_domain(self):
        assert pp.detect_domain("erzähl mir was über das wetter heute") is None


class TestDetectPhase:
    def test_planning_prompt(self):
        assert pp.detect_phase("lass uns ein konzept für X planen") == "planning"

    def test_quiet_prompt(self):
        assert pp.detect_phase("fix den fehler in zeile 12") == "quiet"


class TestBuildRagRouting:
    def test_domain_only(self):
        out = pp.build_rag_routing("ui-frontend", "quiet")
        assert len(out) == 1 and "UI-/Design-Skills" in out[0]

    def test_planning_adds_line(self):
        out = pp.build_rag_routing("workflow", "planning")
        assert len(out) == 2 and any("Sparring" in l for l in out)

    def test_nothing_relevant(self):
        assert pp.build_rag_routing(None, "quiet") == []

    def test_planning_without_domain(self):
        out = pp.build_rag_routing(None, "planning")
        assert len(out) == 1 and "SE-Wissensbasis" in out[0]


class TestComposeContext:
    def test_empty_when_nothing(self):
        assert pp.compose_context(None, "quiet", [], None) == ""

    def test_contains_echo_and_routing(self):
        out = pp.compose_context("ui-frontend", "quiet", ["tu X"], None)
        assert "↳ prelude · [quiet] [ui-frontend]" in out
        assert "RAG-AUFTRAG" in out and "- tu X" in out
        assert out.startswith("<prompt_prelude") and out.endswith("</prompt_prelude>")

    def test_capabilities_block(self):
        out = pp.compose_context("debug", "quiet", ["y"], ["skill:diagnose-hitl"])
        assert "skill:diagnose-hitl" in out


class TestMakeOutput:
    def test_empty_passthrough(self):
        assert pp.make_output("") == ""

    def test_valid_contract(self):
        raw = pp.make_output("<prompt_prelude>x</prompt_prelude>")
        obj = _json.loads(raw)
        assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert obj["hookSpecificOutput"]["additionalContext"] == "<prompt_prelude>x</prompt_prelude>"


class TestDedupe:
    def test_key_domain(self):
        assert pp.dedupe_key("ui-frontend", "quiet") == "ui-frontend"

    def test_key_planning_only(self):
        assert pp.dedupe_key(None, "planning") == "_planning_"

    def test_roundtrip(self, tmp_state_dir):
        assert pp.load_fired("s1", tmp_state_dir) == set()
        pp.save_fired("s1", tmp_state_dir, {"debug", "workflow"})
        assert pp.load_fired("s1", tmp_state_dir) == {"debug", "workflow"}

    def test_missing_returns_empty(self, tmp_state_dir):
        assert pp.load_fired("nope", tmp_state_dir) == set()


class TestTelemetry:
    def test_append_jsonl(self, tmp_state_dir):
        import os
        log = os.path.join(tmp_state_dir, "t.jsonl")
        pp.log_telemetry({"a": 1}, log)
        pp.log_telemetry({"b": 2}, log)
        lines = open(log, encoding="utf-8").read().strip().splitlines()
        assert len(lines) == 2 and _json.loads(lines[0])["a"] == 1

    def test_failsoft_bad_path(self):
        pp.log_telemetry({"a": 1}, "Z:/does/not/exist/t.jsonl")  # darf nicht werfen


class TestExtractQuery:
    def test_strips_stopwords_keeps_domain(self):
        q = pp.extract_query("ich habe einen bug im traceback was kann ich machen")
        assert "debug" in q and "ich" not in q.split()

    def test_caps_length(self):
        q = pp.extract_query("wort " * 40)
        assert len(q.split()) <= 12


class TestQueryAtlas:
    def test_finds_known_record(self, fake_atlas_db):
        out = pp.query_atlas("debugging", fake_atlas_db, limit=3)
        assert "skill:diagnose-hitl" in out

    def test_empty_terms(self, fake_atlas_db):
        assert pp.query_atlas("   ", fake_atlas_db) == []

    def test_failsoft_bad_db(self):
        assert pp.query_atlas("debugging", "Z:/nope/bm25.db") == []


class TestFindAtlasDb:
    def test_missing_root(self, tmp_path):
        assert pp.find_atlas_db(str(tmp_path)) is None


class TestRun:
    def test_trivial_prompt_no_output(self, tmp_path):
        out = pp.run({"prompt": "ja", "session_id": "s"},
                     atlas_root="x", state_dir=str(tmp_path), log_path=str(tmp_path / "l"), now=1.0)
        assert out == ""

    def test_ui_prompt_emits_valid_json(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        out = pp.run({"prompt": "baue ein responsive component layout für den header", "session_id": "s"},
                     atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(tmp_path / "l"), now=1.0)
        obj = _json.loads(out)
        assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "ui-frontend" in obj["hookSpecificOutput"]["additionalContext"]

    def test_dedupe_second_call_silent(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        kw = dict(atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(tmp_path / "l"), now=1.0)
        p = {"prompt": "baue ein responsive component layout für den header", "session_id": "s"}
        assert pp.run(p, **kw) != ""
        assert pp.run(p, **kw) == ""   # zweiter UI-Prompt derselben Session = still

    def test_corrupt_payload_no_throw(self, tmp_path):
        out = pp.run({}, atlas_root="x", state_dir=str(tmp_path), log_path=str(tmp_path / "l"), now=1.0)
        assert out == ""

    # --- Härtung nach Codex-Gesamt-Review ---
    def test_nondict_payload_no_throw(self, tmp_path):
        out = pp.run([], atlas_root="x", state_dir=str(tmp_path), log_path=str(tmp_path / "l"), now=1.0)
        assert out == ""


class TestSessionSanitize:
    def test_no_path_traversal(self, tmp_path):
        state = tmp_path / "st"
        state.mkdir()
        pp.save_fired("../../../evil", str(state), {"x"})
        # alle erzeugten Dateien liegen INNERHALB von state_dir
        assert set(p.name for p in tmp_path.iterdir()) == {"st"}
        assert len(list(state.iterdir())) == 1
        # roundtrip mit derselben (sanitierten) id bleibt konsistent
        assert pp.load_fired("../../../evil", str(state)) == {"x"}

    def test_empty_session_id_defaults(self, tmp_path):
        pp.save_fired("", str(tmp_path), {"z"})
        assert pp.load_fired("", str(tmp_path)) == {"z"}
