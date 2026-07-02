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

    # --- Regression Befund 1: Substring-Fehlklassifikation ("ui" in build/guide/quiet) ---
    def test_build_is_not_ui(self):
        assert pp.detect_domain("bitte den build neu starten") is None

    def test_guide_is_not_ui(self):
        assert pp.detect_domain("ein guide für git") is None

    def test_quiet_is_not_ui(self):
        assert pp.detect_domain("sei ganz quiet") is None

    def test_real_ui_word_matches(self):
        assert pp.detect_domain("baue mir ein UI") == "ui-frontend"

    def test_frontend_layout_matches(self):
        assert pp.detect_domain("frontend layout") == "ui-frontend"

    def test_new_ui_keywords(self):
        assert pp.detect_domain("die oberfläche überarbeiten") == "ui-frontend"
        assert pp.detect_domain("das interface anpassen") == "ui-frontend"

    def test_prefix_stem_matches_inflection(self):
        # "implementier*" muss "implementieren" matchen (Präfix-Stem)
        assert pp.detect_domain("kannst du das implementieren") == "code-impl"

    def test_match_domain_returns_hits(self):
        domain, hits = pp.match_domain("mach das component layout responsive")
        assert domain == "ui-frontend"
        assert set(hits) == {"component", "layout", "responsive"}

    def test_match_domain_no_hits(self):
        assert pp.match_domain("erzähl mir vom wetter heute") == (None, [])


class TestDetectPhase:
    def test_planning_prompt(self):
        assert pp.detect_phase("lass uns ein konzept für X planen") == "planning"

    def test_quiet_prompt(self):
        assert pp.detect_phase("fix den fehler in zeile 12") == "quiet"

    # --- Befund 6a: erweiterte PLANNING_TRIGGERS aus NOTES-live-findings Befund 2 ---
    def test_new_planning_triggers(self):
        for p in ["lass uns das mal durchspielen",
                  "wir müssen den scope klären",
                  "ist das überhaupt machbar",
                  "prüfe die machbarkeit davon",
                  "ist der ansatz durchführbar",
                  "mach eine feasibility einschätzung",
                  "bau erstmal ein grundgerüst",
                  "erstmal nur die hülle bauen"]:
            assert pp.detect_phase(p) == "planning", p

    def test_match_phase_returns_hits(self):
        phase, hits = pp.match_phase("lass uns ein konzept planen")
        assert phase == "planning" and "konzept" in hits


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

    def test_contains_routing_without_echo_by_default(self, monkeypatch):
        # Befund 5: ECHO-Zeile nur mit PRELUDE_ECHO=1, Default aus
        monkeypatch.delenv("PRELUDE_ECHO", raising=False)
        out = pp.compose_context("ui-frontend", "quiet", ["tu X"], None)
        assert "↳ prelude" not in out and "ECHO:" not in out
        assert "RAG-AUFTRAG" in out and "- tu X" in out
        assert out.startswith("<prompt_prelude") and out.endswith("</prompt_prelude>")

    def test_echo_line_with_env_flag(self, monkeypatch):
        monkeypatch.setenv("PRELUDE_ECHO", "1")
        out = pp.compose_context("ui-frontend", "quiet", ["tu X"], None)
        assert "↳ prelude · [quiet] [ui-frontend]" in out

    def test_echo_off_when_flag_not_one(self, monkeypatch):
        monkeypatch.setenv("PRELUDE_ECHO", "0")
        out = pp.compose_context("ui-frontend", "quiet", ["tu X"], None)
        assert "↳ prelude" not in out

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
    def test_key_domain_includes_phase(self):
        # Befund 3: Key = domain+phase, quiet und planning derselben Domain sind getrennt
        assert pp.dedupe_key("ui-frontend", "quiet") == "ui-frontend:quiet"
        assert pp.dedupe_key("ui-frontend", "planning") == "ui-frontend:planning"
        assert pp.dedupe_key("ui-frontend", "quiet") != pp.dedupe_key("ui-frontend", "planning")

    def test_key_planning_only(self):
        assert pp.dedupe_key(None, "planning") == "_planning_"

    def test_rag_reference_detection(self):
        assert pp.has_rag_reference("welche skills hast du dafür")
        assert pp.has_rag_reference("mach eine memory_search danach")
        assert pp.has_rag_reference("check die capability liste")
        assert pp.has_rag_reference("welche fähigkeiten gibt es")
        assert not pp.has_rag_reference("fix den fehler in zeile 12")

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

    # --- Befund 2: BM25-Anbindung war tot (Bindestrich-Domain -> OperationalError) ---
    def test_build_fts_query_quotes_and_ors(self):
        q = pp.build_fts_query("ui-frontend layout")
        assert q == '"ui" OR "frontend" OR "layout"'

    def test_build_fts_query_escapes_quotes(self):
        assert pp.build_fts_query('fo"o') == '"fo""o"'

    def test_hyphenated_domain_prefix_hits(self, fake_atlas_db):
        # exakt der Live-Fehlerfall: Domain-Präfix mit Bindestrich vorne dran
        out = pp.query_atlas("ui-frontend component layout", fake_atlas_db, limit=3)
        assert "skill:frontend-design" in out

    def test_extract_query_output_hits_atlas(self, fake_atlas_db):
        # Integrationstest: echter extract_query-Output MIT Domain-Präfix durch die Fake-FTS5-DB
        q = pp.extract_query("baue mir ein ui component layout für die seite")
        assert "ui-frontend" in q  # Präfix ist wirklich drin
        out = pp.query_atlas(q, fake_atlas_db, limit=3)
        assert "skill:frontend-design" in out


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

    # --- Befund 3: quiet->planning derselben Domain feuert erneut ---
    def test_phase_change_refires_same_domain(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        kw = dict(atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(tmp_path / "l"), now=1.0)
        quiet = {"prompt": "baue ein responsive component layout für den header", "session_id": "s"}
        planning = {"prompt": "lass uns ein konzept für das responsive layout planen", "session_id": "s"}
        assert pp.run(quiet, **kw) != ""       # ui-frontend:quiet feuert
        assert pp.run(planning, **kw) != ""    # ui-frontend:planning feuert TROTZ gleicher Domain
        assert pp.run(quiet, **kw) == ""       # gleicher Key erneut = still

    # --- Befund 3: expliziter RAG-Bezug re-armt den Dedupe ---
    def test_rag_reference_rearms_dedupe(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        kw = dict(atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(tmp_path / "l"), now=1.0)
        first = {"prompt": "baue ein responsive component layout für den header", "session_id": "s"}
        rag = {"prompt": "welche skills hast du für das frontend layout thema", "session_id": "s"}
        assert pp.run(first, **kw) != ""
        assert pp.run(first, **kw) == ""   # normaler Dedupe greift weiter
        assert pp.run(rag, **kw) != ""     # expliziter RAG-Bezug feuert trotz Dedupe

    # --- Befund 4: fired-Event ist auditierbar ---
    def test_fired_event_telemetry_fields(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        log = tmp_path / "l"
        p = {"prompt": "baue ein responsive component layout für den header", "session_id": "s"}
        assert pp.run(p, atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(log), now=1.0) != ""
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["fired"] is True
        assert ev["prompt_preview"] == p["prompt"][:80]
        assert set(ev["matched_keywords"]) >= {"component", "layout", "responsive"}
        assert ev["caps_count"] == len(ev["caps"]) and ev["caps_count"] >= 1
        assert ev["rearmed"] is False

    def test_skip_event_has_prompt_preview(self, tmp_path):
        log = tmp_path / "l"
        pp.run({"prompt": "erzähl mir bitte was über das wetter morgen früh", "session_id": "s"},
               atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(log), now=1.0)
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["skip"] == "no_routing"
        assert ev["prompt_preview"].startswith("erzähl mir")


class TestCleanupState:
    def test_removes_old_keeps_fresh(self, tmp_path):
        import os
        old = tmp_path / "fired_old.json"
        fresh = tmp_path / "fired_fresh.json"
        old.write_text("[]"); fresh.write_text("[]")
        now = 1_000_000_000.0
        os.utime(old, (now - 8 * 86400, now - 8 * 86400))
        os.utime(fresh, (now - 3600, now - 3600))
        pp.cleanup_state(str(tmp_path), now)
        assert not old.exists() and fresh.exists()

    def test_failsoft_missing_dir(self, tmp_path):
        pp.cleanup_state(str(tmp_path / "gibtsnicht"), 1.0)  # darf nicht werfen


class TestMain:
    def _isolate(self, monkeypatch, tmp_path):
        monkeypatch.setattr(pp, "_default_log_path", lambda: str(tmp_path / "log.jsonl"))
        monkeypatch.setattr(pp, "_default_state_dir", lambda: str(tmp_path / "st"))
        return tmp_path / "log.jsonl"

    def test_bad_stdin_logs_skip_and_exits_zero(self, monkeypatch, tmp_path, capsys):
        import io, sys
        log = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "abgeschnitt'))
        assert pp.main() == 0
        assert capsys.readouterr().out == ""  # nie blockieren, kein Output
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["skip"] == "bad_stdin"
        assert ev["prompt_preview"] == '{"prompt": "abgeschnitt'  # rohes stdin, 80-Zeichen-Cap

    def test_crash_logs_event_and_exits_zero(self, monkeypatch, tmp_path, capsys):
        import io, sys
        log = self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "x"}'))
        def boom(*a, **k):
            raise ValueError("kaboom")
        monkeypatch.setattr(pp, "run", boom)
        assert pp.main() == 0
        assert capsys.readouterr().out == ""
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["skip"] == "crash" and "kaboom" in ev["error"]
        assert ev["prompt_preview"] == "x"

    def test_crash_logging_itself_failsoft(self, monkeypatch, tmp_path, capsys):
        import io, sys
        # selbst wenn auch das Crash-Logging wirft: Exit 0, kein Output
        monkeypatch.setattr(pp, "_default_log_path", lambda: str(tmp_path / "log.jsonl"))
        monkeypatch.setattr(pp, "_default_state_dir", lambda: str(tmp_path / "st"))
        monkeypatch.setattr(sys, "stdin", io.StringIO('{"prompt": "x"}'))
        monkeypatch.setattr(pp, "run", lambda *a, **k: (_ for _ in ()).throw(ValueError("x")))
        monkeypatch.setattr(pp, "log_telemetry", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
        assert pp.main() == 0
        assert capsys.readouterr().out == ""


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
