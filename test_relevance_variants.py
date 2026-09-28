# -*- coding: utf-8 -*-
"""v11-Kandidaten (docs/2026-09-28-plan-relevanz-replay.md): reine Funktionen
für Projekt-Anker (Idee 1) und Gesprächskontext (Idee 2). Kein I/O — der
Replay-Harness und später der Hook rufen dieselben Funktionen."""
import prompt_prelude as pp


class TestProjectSlugs:
    def test_nested_project_most_specific_first(self):
        assert pp.project_slugs(r"C:\Users\domes\AI\Hooks-bau\prompt-prelude") == \
            ["prompt-prelude", "hooks-bau"]

    def test_underscores_normalized(self):
        assert pp.project_slugs(r"C:\Users\domes\dynamic_central_orchestrator") == \
            ["dynamic-central-orchestrator"]

    def test_generic_roots_yield_nothing(self):
        # Home, AI-Workspace, Desktop sind Sammelordner, kein Projekt.
        for cwd in (r"C:\Users\domes", r"C:\Users\domes\AI", r"C:\Users\domes\Desktop",
                    r"C:\Users\domes\Desktop\Claude-Projekte"):
            assert pp.project_slugs(cwd) == [], cwd

    def test_forward_slashes_and_trailing_sep(self):
        assert pp.project_slugs("C:/Users/domes/AI/evolab/") == ["evolab"]

    def test_depth_capped_at_two(self):
        # Tiefe Unterordner (src/, tests/) sind kein Projektname.
        assert pp.project_slugs(r"C:\Users\domes\AI\membrain\src\core") == \
            ["membrain"]

    def test_empty_and_garbage_safe(self):
        assert pp.project_slugs("") == []
        assert pp.project_slugs(None) == []


class TestRecordMatchesSlugs:
    def test_matches_across_separator_styles(self):
        slugs = ["dynamic-central-orchestrator"]
        assert pp.record_matches_slugs("atlas/project-deep:dynamic-central-orchestrator", slugs)
        assert pp.record_matches_slugs("agent-memory/dynamic_central_orchestrator/learnings/L140", slugs)

    def test_requires_token_boundary(self):
        # "loop" darf nicht in "dome-loop-x" … wohl aber als ganzer Slug matchen.
        assert not pp.record_matches_slugs("atlas/project-deep:dome-loops", ["dome-loop"])
        assert pp.record_matches_slugs("haupt-wiki/queries/2026-07-19-session-dome-loop.md",
                                       ["dome-loop"])

    def test_no_slugs_no_match(self):
        assert not pp.record_matches_slugs("atlas/project:x", [])


class TestProjectQuery:
    # Replay-Befund 2026-09-28: bloßes Umsortieren der Top 12 änderte in 0/154
    # Prompts etwas — Projekt-Records sind selten in den Treffern. Der Anker
    # muss aktiv suchen: Projektname als Suchbegriff (41 -> 64/154 Abdeckung).
    def test_appends_most_specific_slug_as_words(self):
        assert pp.project_query("lighthouse runner", ["prompt-prelude", "hooks-bau"]) == \
            "lighthouse runner prompt prelude"

    def test_no_slugs_no_query(self):
        assert pp.project_query("lighthouse runner", []) == ""

    def test_slug_already_in_terms_not_duplicated(self):
        assert pp.project_query("evolab runner", ["evolab"]) == "evolab runner"


class TestSelectProjectHits:
    RES = [{"record_id": "atlas/skill:a"},
           {"record_id": "agent-memory/prompt_prelude/learnings/L13", "heading": "L13"},
           {"record_id": "atlas/project:crazy-professor"},
           {"record_id": "docs-prompt-prelude/README.md", "heading": "README"},
           {"record_id": "atlas/project-deep:prompt-prelude", "heading": "Tiefe"}]

    def test_only_slug_matches_in_order_capped(self):
        out = pp.select_project_hits(self.RES, ["prompt-prelude"], limit=2)
        assert [r["record_id"] for r in out] == [
            "agent-memory/prompt_prelude/learnings/L13", "docs-prompt-prelude/README.md"]

    def test_excludes_already_injected(self):
        out = pp.select_project_hits(self.RES, ["prompt-prelude"], limit=2,
                                     exclude={"agent-memory/prompt_prelude/learnings/L13"})
        assert [r["record_id"] for r in out] == [
            "docs-prompt-prelude/README.md", "atlas/project-deep:prompt-prelude"]

    def test_garbage_and_empty_safe(self):
        assert pp.select_project_hits(None, ["x"]) == []
        assert pp.select_project_hits(self.RES, []) == []
        assert pp.select_project_hits([None, 3, {"record_id": "a/x-y"}], ["x-y"]) == \
            [{"record_id": "a/x-y"}]


class TestContextTerms:
    PREV = ("Der Automaten-Filter ist gebaut. Headless-Läufe werden über "
            "CLAUDE_CODE_ENTRYPOINT erkannt; der Automaten-Filter schreibt "
            "entrypoint in die Telemetrie. Telemetrie v10 ist live.")

    def test_frequency_ranked_significant_terms(self):
        terms = pp.context_terms(self.PREV, prompt_terms="", limit=3)
        assert terms[:2] == ["automaten-filter", "telemetrie"]

    def test_excludes_terms_already_in_prompt(self):
        terms = pp.context_terms(self.PREV, prompt_terms="telemetrie auswerten", limit=5)
        assert "telemetrie" not in terms

    def test_ignores_code_fences_and_short_tokens(self):
        prev = "```python\nimport verylongidentifier\n```\nDaemon Daemon läuft."
        assert pp.context_terms(prev, prompt_terms="", limit=5) == ["daemon"]

    def test_empty_safe(self):
        assert pp.context_terms(None, prompt_terms="", limit=5) == []
        assert pp.context_terms("", prompt_terms=None, limit=5) == []
