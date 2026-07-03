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

    # --- Iteration 1: maschinell generierte Prompts (Subagent-Callbacks etc.) skippen.
    # Live-Befund 2026-07-02: <task-notification>-Prompts dominierten fired-Events
    # mehrerer Sessions und produzierten Fehl-Routings (ui-frontend auf Telemetrie-Reports).
    def test_task_notification_skips(self):
        p = ("<task-notification>\n<task-id>abc123</task-id>\n<status>completed</status>\n"
             "<summary>Agent finished with many words in the summary line</summary>\n"
             "</task-notification>")
        assert pp.should_skip(p) == (True, "machine_prompt")

    def test_system_reminder_skips(self):
        p = "<system-reminder>\nirgendein injizierter kontext mit vielen wörtern drin\n</system-reminder>"
        assert pp.should_skip(p) == (True, "machine_prompt")

    def test_all_machine_markers_skip(self):
        # Codex-Verifier-Finding: alle 4 Marker abdecken, nicht nur 2
        for marker in pp.MACHINE_PROMPT_MARKERS:
            p = f"{marker}\nirgendein harness-generierter inhalt mit vielen wörtern\n"
            assert pp.should_skip(p) == (True, "machine_prompt"), marker

    def test_machine_marker_case_insensitive_with_leading_space(self):
        p = "   <TASK-NOTIFICATION>\n<task-id>x</task-id>\nviele wörter hier drin\n</TASK-NOTIFICATION>"
        assert pp.should_skip(p) == (True, "machine_prompt")

    def test_xml_in_user_text_is_not_machine(self):
        # User-Text, der zufällig Tags ENTHÄLT (nicht damit beginnt), bleibt normal
        skip, reason = pp.should_skip("kannst du erklären was <div> im html layout macht")
        assert reason != "machine_prompt"


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

    # --- Iteration 1: Agent-Tooling-Vokabular (Hooks/Skills/MCP) routet nach workflow.
    # Live-Befund: Meta-Arbeit an Hooks/Skills lief als no_routing oder Fehl-Routing.
    def test_agent_tooling_keywords_route_to_workflow(self):
        assert pp.detect_domain("der hook feuert bei mir nicht wie erwartet") == "workflow"
        assert pp.detect_domain("bau mir dafür einen neuen mcp server") == "workflow"
        assert pp.detect_domain("welcher skill passt zu dieser aufgabe am besten") == "workflow"

    # --- Codex-Verifier-Finding: Debug-Signale müssen Agent-Tooling-Wörter schlagen.
    # "Debugge einen React Hook: useEffect wirft Exception" darf nicht workflow werden.
    def test_debug_beats_agent_tooling_keywords(self):
        assert pp.detect_domain("debugge einen react hook: useEffect wirft eine exception") == "debug"
        assert pp.detect_domain("der hook crasht mit einem traceback") == "debug"


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

    # --- Iteration 1 (H1): Wording ohne Selbst-Entwertung. H4-Befund: 3% Compliance
    # vs. 2% Skip-Baseline — "kein Befehl"/"optional" gaben dem Modell explizit
    # die Erlaubnis wegzuschauen.
    def test_no_self_devaluation_wording(self):
        out = pp.compose_context("debug", "quiet", ["y"], ["skill:diagnose-hitl"])
        assert "kein Befehl" not in out
        assert "optional" not in out.lower()
        assert "MÖGLICHERWEISE" not in out

    def test_caps_presented_as_executed_search(self):
        # Treffer sind vorgezogenes Suchergebnis (lesen statt selbst suchen)
        out = pp.compose_context("debug", "quiet", ["y"], ["skill:diagnose-hitl"])
        assert "bereits ausgeführt" in out

    def test_query_renders_ready_search_call(self):
        out = pp.compose_context("debug", "quiet", ["y"], ["skill:x"], query="debug traceback parser")
        assert 'memory_search_tool("debug traceback parser")' in out

    def test_no_query_no_search_call_line(self):
        out = pp.compose_context("debug", "quiet", ["y"], ["skill:x"])
        assert "memory_search_tool(" not in out


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

    # --- Iteration 1: Schema-Version an jedem Event (Alt-Events ohne routing_source
    # verzerrten die A/B-Auswertung; ab jetzt ist der Schema-Stand explizit).
    def test_schema_version_stamped(self, tmp_state_dir):
        import os
        log = os.path.join(tmp_state_dir, "t.jsonl")
        pp.log_telemetry({"a": 1}, log)
        ev = _json.loads(open(log, encoding="utf-8").read().strip())
        assert ev["v"] == pp.TELEMETRY_SCHEMA_VERSION == 3


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

    # --- Iteration 1: machine_prompt-Skip auf run()-Ebene inkl. Telemetrie-Grund ---
    def test_machine_prompt_skips_with_reason(self, tmp_path):
        log = tmp_path / "l"
        p = ("<task-notification>\n<task-id>x</task-id>\n"
             "<summary>Agent finished analyzing the telemetry data files</summary>\n"
             "</task-notification>")
        out = pp.run({"prompt": p, "session_id": "s"},
                     atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(log), now=1.0)
        assert out == ""
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["skip"] == "machine_prompt"

    # --- Iteration 1: fired-Kontext enthält fertige memory_search_tool-Query ---
    def test_fired_context_contains_ready_query(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        out = pp.run({"prompt": "baue ein responsive component layout für den header", "session_id": "s"},
                     atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(tmp_path / "l"), now=1.0)
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert 'memory_search_tool("' in ctx

    def test_skip_event_has_prompt_preview(self, tmp_path):
        # v3: substantielle Prompts feuern jetzt (general-Fallback) — für die
        # Preview-auf-Skip-Prüfung einen weiterhin-übersprungenen too_short-Fall nehmen.
        log = tmp_path / "l"
        pp.run({"prompt": "mach schnell", "session_id": "s"},
               atlas_root="x", state_dir=str(tmp_path / "st"), log_path=str(log), now=1.0)
        ev = _json.loads(log.read_text(encoding="utf-8").strip().splitlines()[-1])
        assert ev["skip"] == "too_short"
        assert ev["prompt_preview"].startswith("mach schnell")


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

    def test_fire_prints_json_with_system_message(self, monkeypatch, tmp_path, capsys):
        # v3: der echte main()-stdin-Pfad muss beim Feuern gültiges JSON mit der
        # sichtbaren systemMessage drucken (Daemon down via conftest -> general-Fallback).
        import io, sys
        self._isolate(monkeypatch, tmp_path)
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: None)  # hermetisch
        monkeypatch.setattr(sys, "stdin", io.StringIO(
            _json.dumps({"prompt": "erzähl mir bitte was über das wetter morgen früh",
                         "session_id": "m"})))
        assert pp.main() == 0
        obj = _json.loads(capsys.readouterr().out.strip())
        assert obj["systemMessage"].startswith("prelude ▸")
        assert obj["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"

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


# ---------------------------------------------------------------------------
# H2: Semantisches Routing über den Atlas-Daemon (gemockt via injizierbarem
# http_fn — kein Test trifft je den echten Daemon, s. conftest no_real_daemon).
# ---------------------------------------------------------------------------

def _scores(*pairs):
    return {"scores": [{"name": n, "score": s} for n, s in pairs]}


def _mk_http(classify=None, search=None, calls=None):
    """Fake-http_fn: routet nach URL-Suffix. None für einen Endpoint -> ConnectionError.
    Ein Wert kann auch eine Exception-INSTANZ sein -> wird geworfen."""
    def fn(url, body, timeout):
        if calls is not None:
            calls.append(url)
        resp = classify if url.endswith("/classify") else search
        if isinstance(resp, Exception):
            raise resp
        if resp is None and not url.endswith("/search"):
            raise ConnectionError("classify down")
        if resp is None and url.endswith("/search"):
            raise ConnectionError("search down")
        return resp
    return fn


# Prompt OHNE Keyword-Treffer (weder Domain noch Planning) — nur der Daemon
# kann ihn routen. Lang genug für should_skip.
NO_KEYWORD_PROMPT = "mach die seite bitte etwas schöner und deutlich moderner insgesamt"
# Prompt MIT eindeutigen ui-frontend-Keywords für Fallback-Tests.
KEYWORD_UI_PROMPT = "baue ein responsive component layout für den header"


class TestClassifyViaDaemon:
    def test_success_sorted_desc(self):
        fn = _mk_http(classify=_scores(("debug", 0.2), ("ui-frontend", 0.7)))
        out = pp.classify_via_daemon("x", http_fn=fn)
        assert [s["name"] for s in out] == ["ui-frontend", "debug"]

    def test_timeout_returns_none(self):
        fn = _mk_http(classify=TimeoutError("slow"))
        assert pp.classify_via_daemon("x", http_fn=fn) is None

    def test_non200_returns_none(self):
        # _http_post_json liefert None bei Non-200 -> classify muss None geben
        fn = _mk_http(classify={"scores": None})
        assert pp.classify_via_daemon("x", http_fn=lambda u, b, t: None) is None
        assert pp.classify_via_daemon("x", http_fn=fn) is None  # Schema-Drift ebenso

    def test_prompt_capped_to_500(self):
        seen = {}
        def fn(url, body, timeout):
            seen.update(body)
            return _scores(("debug", 0.9))
        pp.classify_via_daemon("x" * 2000, http_fn=fn)
        assert len(seen["query"]) == 500
        # 6 Domains + Null-Anker (Kalibrier-Befund: High-Score-FP auf Meta-Fragen)
        assert len(seen["labels"]) == 6 + len(pp.NULL_ANCHORS)
        known = {**pp.DOMAIN_DESCRIPTIONS, **pp.NULL_ANCHORS}
        assert all(l["name"] in known for l in seen["labels"])


class TestPickDaemonDomain:
    def test_clear_winner_accepted(self):
        assert pp.pick_daemon_domain([{"name": "debug", "score": 0.62},
                                      {"name": "code-impl", "score": 0.40}]) == "debug"

    def test_below_accept_rejected(self):
        assert pp.pick_daemon_domain([{"name": "debug", "score": 0.30},
                                      {"name": "code-impl", "score": 0.10}]) is None

    def test_narrow_margin_below_clear_rejected(self):
        # 0.40 vs 0.38: Margin < TH_MARGIN und Score < TH_CLEAR -> ablehnen
        assert pp.pick_daemon_domain([{"name": "debug", "score": 0.40},
                                      {"name": "code-impl", "score": 0.38}]) is None

    def test_narrow_margin_but_clear_accepted(self):
        # >= TH_CLEAR gewinnt auch bei knappem Abstand
        assert pp.pick_daemon_domain([{"name": "debug", "score": 0.55},
                                      {"name": "code-impl", "score": 0.53}]) == "debug"

    def test_unknown_label_rejected(self):
        assert pp.pick_daemon_domain([{"name": "totally-new", "score": 0.9}]) is None

    def test_null_anchor_win_rejected(self):
        # Gewinnt der Meta-Null-Anker, ist das KEINE Domain -> Keyword-Fallback
        assert pp.pick_daemon_domain([{"name": "meta-none", "score": 0.61},
                                      {"name": "workflow", "score": 0.55}]) is None

    def test_null_anchor_close_second_vetoes(self):
        assert pp.pick_daemon_domain([{"name": "workflow", "score": 0.60},
                                      {"name": "meta-none", "score": 0.52}]) is None

    def test_null_anchor_close_third_vetoes(self):
        # Echter Kalibrier-Fall ("welche projekte liegen in meinem AI ordner"):
        # der Anker steht auf Platz 3 — das Veto muss ALLE Plätze scannen.
        assert pp.pick_daemon_domain([{"name": "workflow", "score": 0.547},
                                      {"name": "code-impl", "score": 0.463},
                                      {"name": "meta-none", "score": 0.442}]) is None

    def test_null_anchor_distant_second_no_veto(self):
        assert pp.pick_daemon_domain([{"name": "debug", "score": 0.62},
                                      {"name": "meta-none", "score": 0.40}]) == "debug"

    def test_none_and_empty(self):
        assert pp.pick_daemon_domain(None) is None
        assert pp.pick_daemon_domain([]) is None


class TestFormatCapHint:
    def test_with_heading(self):
        r = {"record_id": "skill:frontend-design", "heading": "Frontend  Design\nGuide"}
        assert pp.format_cap_hint(r) == "skill:frontend-design — Frontend Design Guide"

    def test_record_id_only(self):
        assert pp.format_cap_hint({"record_id": "skill:x"}) == "skill:x"

    def test_snippet_fallback_and_cap(self):
        r = {"record_id": "r1", "snippet": "s" * 200}
        out = pp.format_cap_hint(r)
        assert out.startswith("r1 — ") and len(out) <= len("r1 — ") + 60

    def test_broken_input(self):
        assert pp.format_cap_hint(None) == ""


class TestDaemonBudget:
    def test_exhausted_skips_call(self):
        b = pp.DaemonBudget(budget_s=0.0)
        called = []
        assert b.call(lambda: called.append(1) or "x") is None
        assert called == []

    def test_spent_accumulates_and_exhausts(self):
        b = pp.DaemonBudget(budget_s=0.001)
        import time as _t
        b.call(lambda: _t.sleep(0.002))
        assert b.exhausted()
        assert b.call(lambda: "never") is None


class TestRunSemanticRouting:
    def _kw(self, tmp_path):
        return dict(atlas_root="x", state_dir=str(tmp_path / "st"),
                    log_path=str(tmp_path / "l"), now=1.0)

    def _last_event(self, tmp_path):
        return _json.loads((tmp_path / "l").read_text(encoding="utf-8").strip().splitlines()[-1])

    def test_daemon_routes_prompt_without_keywords(self, tmp_path):
        fn = _mk_http(classify=_scores(("ui-frontend", 0.62), ("debug", 0.20)),
                      search={"results": []})
        out = pp.run({"prompt": NO_KEYWORD_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        assert "ui-frontend" in out
        ev = self._last_event(tmp_path)
        assert ev["routing_source"] == "daemon"
        assert ev["keyword_domain"] is None
        assert ev["daemon_top"][0] == {"name": "ui-frontend", "score": 0.62}

    def test_timeout_falls_back_to_keywords(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        fn = _mk_http(classify=TimeoutError("slow"), search=TimeoutError("slow"))
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        assert "ui-frontend" in out
        ev = self._last_event(tmp_path)
        assert ev["routing_source"] == "keywords"
        assert ev["daemon_top"] == []

    def test_non200_falls_back_to_keywords(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=lambda u, b, t: None, **self._kw(tmp_path))
        assert "ui-frontend" in out
        assert self._last_event(tmp_path)["routing_source"] == "keywords"

    def test_threshold_rejection_falls_back(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        # Daemon antwortet, aber unter TH_ACCEPT -> Keyword-Fallback,
        # daemon_top bleibt trotzdem im Log (A/B-Vergleich)
        fn = _mk_http(classify=_scores(("research", 0.22), ("debug", 0.21)),
                      search={"results": []})
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        assert "ui-frontend" in out
        ev = self._last_event(tmp_path)
        assert ev["routing_source"] == "keywords"
        assert ev["daemon_top"][0]["name"] == "research"

    def test_fallback_fire_carries_ab_fields(self, tmp_path, monkeypatch):
        # v3: weder Daemon (down) noch Keywords -> general-Fallback FEUERT jetzt,
        # A/B-Felder bleiben am fired-Event erhalten (routing_source=fallback).
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: None)  # hermetisch, keine caps
        pp.run({"prompt": "erzähl mir bitte was über das wetter morgen früh", "session_id": "s"},
               http_fn=_mk_http(), **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["fired"] is True
        assert ev["domain"] == "general"
        assert ev["routing_source"] == "fallback"
        assert ev["keyword_domain"] is None and ev["daemon_top"] == []

    def test_fired_telemetry_has_all_new_fields(self, tmp_path):
        fn = _mk_http(classify=_scores(("ui-frontend", 0.62)),
                      search={"results": [{"record_id": "skill:frontend-design",
                                           "heading": "Frontend Design", "score": 0.8}]})
        assert pp.run({"prompt": NO_KEYWORD_PROMPT, "session_id": "s"},
                      http_fn=fn, **self._kw(tmp_path)) != ""
        ev = self._last_event(tmp_path)
        for field in ("routing_source", "daemon_top", "keyword_domain",
                      "daemon_latency_ms", "caps_source"):
            assert field in ev, field
        assert isinstance(ev["daemon_latency_ms"], float)

    def test_caps_via_daemon_with_heading(self, tmp_path):
        fn = _mk_http(classify=_scores(("ui-frontend", 0.62)),
                      search={"results": [{"record_id": "skill:frontend-design",
                                           "heading": "Frontend Design Guide",
                                           "source_path": "y.md", "score": 0.8}]})
        out = pp.run({"prompt": NO_KEYWORD_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        assert "skill:frontend-design — Frontend Design Guide" in out
        ev = self._last_event(tmp_path)
        assert ev["caps_source"] == "daemon"
        assert ev["caps"] == ["skill:frontend-design — Frontend Design Guide"]

    def test_caps_daemon_error_falls_back_to_sqlite(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        fn = _mk_http(classify=_scores(("ui-frontend", 0.62)), search=ConnectionError("down"))
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["caps_source"] == "sqlite"
        assert "skill:frontend-design" in out

    def test_budget_guard_skips_all_daemon_calls(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        calls = []
        fn = _mk_http(classify=_scores(("debug", 0.9)), search={"results": []}, calls=calls)
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=fn, budget=pp.DaemonBudget(budget_s=0.0), **self._kw(tmp_path))
        assert calls == []                      # Budget erschöpft: kein einziger Daemon-Call
        assert "ui-frontend" in out             # Keyword-Fallback trägt
        ev = self._last_event(tmp_path)
        assert ev["routing_source"] == "keywords" and ev["caps_source"] == "sqlite"

    def test_classify_down_skips_search_entirely(self, tmp_path, monkeypatch, fake_atlas_db):
        # Windows-Befund: connection-refused auf localhost kostet den VOLLEN
        # Timeout. classify-Fehler => Daemon gilt als down => kein /search-Call.
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        calls = []
        fn = _mk_http(classify=ConnectionError("down"), search={"results": []}, calls=calls)
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=fn, **self._kw(tmp_path))
        assert [u for u in calls if u.endswith("/classify")]
        assert not [u for u in calls if u.endswith("/search")]
        ev = self._last_event(tmp_path)
        assert ev["routing_source"] == "keywords" and ev["caps_source"] == "sqlite"
        assert "ui-frontend" in out

    def test_budget_exhausted_by_classify_skips_search(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        calls = []
        def slow_classify(url, body, timeout):
            calls.append(url)
            import time as _t
            _t.sleep(0.002)
            return _scores(("ui-frontend", 0.62))
        out = pp.run({"prompt": NO_KEYWORD_PROMPT, "session_id": "s"}, http_fn=slow_classify,
                     budget=pp.DaemonBudget(budget_s=0.001), **self._kw(tmp_path))
        assert [u for u in calls if u.endswith("/classify")]      # classify lief noch
        assert not [u for u in calls if u.endswith("/search")]    # search geskippt
        assert self._last_event(tmp_path)["caps_source"] in ("sqlite", "none")
        assert "ui-frontend" in out


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


# ===========================================================================
# Iteration 2 (v3): breit feuern (general-Fallback) + sichtbare systemMessage
# + Dedupe pro Thema. Motivation: Compliance-Eval zeigte, dass der Advisory-
# Kanal als ANWEISUNG ~3% wirkt; Wert liegt in der Vorab-Injektion + Sichtbarkeit.
# ===========================================================================

class TestV3GeneralFallback:
    def _kw(self, tmp_path):
        return dict(atlas_root="x", state_dir=str(tmp_path / "st"),
                    log_path=str(tmp_path / "l"), now=1.0)

    def _last_event(self, tmp_path):
        return _json.loads((tmp_path / "l").read_text(encoding="utf-8").strip().splitlines()[-1])

    def test_substantive_prompt_without_domain_fires_general(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: None)  # hermetisch
        out = pp.run({"prompt": "erzähl mir bitte was über das wetter morgen früh", "session_id": "s"},
                     http_fn=_mk_http(), **self._kw(tmp_path))
        assert out != ""
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert 'domain="general"' in ctx

    def test_specific_domain_still_wins_over_general(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=_mk_http(), **self._kw(tmp_path))
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert 'domain="ui-frontend"' in ctx

    def test_general_routing_line_present(self, tmp_path, monkeypatch):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: None)
        out = pp.run({"prompt": "erzähl mir bitte was über das wetter morgen früh", "session_id": "s"},
                     http_fn=_mk_http(), **self._kw(tmp_path))
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "memory_search_tool" in ctx

    def test_trivial_still_skips_no_general(self, tmp_path):
        assert pp.run({"prompt": "ok", "session_id": "s"}, **self._kw(tmp_path)) == ""

    def test_machine_prompt_still_skips_no_general(self, tmp_path):
        p = "<task-notification>\n<task-id>x</task-id>\n</task-notification>"
        assert pp.run({"prompt": p, "session_id": "s"}, **self._kw(tmp_path)) == ""

    def test_build_rag_routing_general(self):
        lines = pp.build_rag_routing("general", "quiet")
        assert lines and "memory_search_tool" in lines[0]


class TestV3SystemMessage:
    def _kw(self, tmp_path):
        return dict(atlas_root="x", state_dir=str(tmp_path / "st"),
                    log_path=str(tmp_path / "l"), now=1.0)

    def test_build_system_message_format(self):
        msg = pp.build_system_message("general", "quiet", ["a", "b"], "daemon")
        assert msg == "prelude ▸ general · quiet · caps=2(daemon)"

    def test_build_system_message_zero_caps(self):
        assert pp.build_system_message("debug", "planning", [], "none") == \
            "prelude ▸ debug · planning · caps=0(none)"

    def test_make_output_includes_system_message(self):
        raw = pp.make_output("<x>ctx</x>", system_message="prelude ▸ debug")
        obj = _json.loads(raw)
        assert obj["systemMessage"] == "prelude ▸ debug"
        assert obj["hookSpecificOutput"]["additionalContext"] == "<x>ctx</x>"

    def test_make_output_no_system_message_by_default(self):
        obj = _json.loads(pp.make_output("<x>ctx</x>"))
        assert "systemMessage" not in obj

    def test_make_output_system_message_only(self):
        # Codex-Nit #3: Helfer-Kontrakt gepinnt — systemMessage OHNE Kontext
        # erzeugt ein systemMessage-only-JSON (run() nutzt das nie, aber der
        # Helfer darf es und das Verhalten ist jetzt festgeschrieben).
        obj = _json.loads(pp.make_output("", system_message="prelude ▸ x"))
        assert obj == {"systemMessage": "prelude ▸ x"}
        assert "hookSpecificOutput" not in obj

    def test_fire_emits_visible_system_message(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        out = pp.run({"prompt": KEYWORD_UI_PROMPT, "session_id": "s"},
                     http_fn=_mk_http(), **self._kw(tmp_path))
        obj = _json.loads(out)
        assert obj["systemMessage"].startswith("prelude ▸")
        assert "ui-frontend" in obj["systemMessage"]
        assert "caps=" in obj["systemMessage"]

    def test_skip_has_no_output_no_system_message(self, tmp_path):
        # Q1: sichtbare Zeile NUR wenn der Hook feuert
        assert pp.run({"prompt": "ok", "session_id": "s"}, **self._kw(tmp_path)) == ""


class TestV3TopicDedupe:
    def _kw(self, tmp_path):
        return dict(atlas_root="x", state_dir=str(tmp_path / "st"),
                    log_path=str(tmp_path / "l"), now=1.0)

    def test_dedupe_key_includes_topic_sig(self):
        assert pp.dedupe_key("ui-frontend", "quiet", "abc123") == "ui-frontend:quiet:abc123"

    def test_dedupe_key_backward_compatible_without_sig(self):
        assert pp.dedupe_key("ui-frontend", "quiet") == "ui-frontend:quiet"

    def test_topic_signature_stable_and_order_independent(self):
        a = pp.topic_signature("baue ein responsive layout header")
        b = pp.topic_signature("header layout responsive baue ein")
        c = pp.topic_signature("style den footer button modern")
        assert a == b and a != c

    def test_topic_signature_nonstring_failsoft(self):
        # Codex-Nit #1: defensiv gegen Nicht-Strings (should_skip fängt das zwar
        # vorher ab, aber die Funktion selbst darf nie werfen -> fail-soft).
        for bad in (1234, {"x": 1}, None, ["a"]):
            assert pp.topic_signature(bad) == "0"

    def test_same_topic_repeated_dedupes(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        kw = self._kw(tmp_path)
        p = {"prompt": "baue ein responsive component layout für den header bereich", "session_id": "s"}
        assert pp.run(p, http_fn=_mk_http(), **kw) != ""
        assert pp.run(p, http_fn=_mk_http(), **kw) == ""   # exakt gleiches Thema -> still

    def test_different_topics_same_domain_both_fire(self, tmp_path, monkeypatch, fake_atlas_db):
        monkeypatch.setattr(pp, "find_atlas_db", lambda root: fake_atlas_db)
        kw = self._kw(tmp_path)
        p1 = {"prompt": "baue ein responsive component layout für den header bereich", "session_id": "s"}
        p2 = {"prompt": "style den footer button mit css und mach das interface moderner", "session_id": "s"}
        assert pp.run(p1, http_fn=_mk_http(), **kw) != ""
        assert pp.run(p2, http_fn=_mk_http(), **kw) != ""   # anderes Thema, gleiche Domain -> feuert
