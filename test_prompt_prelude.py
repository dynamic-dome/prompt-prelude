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
