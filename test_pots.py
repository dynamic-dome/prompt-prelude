# -*- coding: utf-8 -*-
"""Runde 4: getrennte Töpfe — reine Funktionen und fail-soft Daemon-Zugriff."""
import prompt_prelude as pp


def _r(rid, snippet="text", heading=""):
    return {"record_id": rid, "snippet": snippet, "heading": heading}


class TestCapabilityKinds:
    def test_capability_cards_are_recognised(self):
        for rid in ("atlas/skill:tdd", "atlas/subagent:explore", "atlas/plugin:codex",
                    "atlas/mcp-server:wiki"):
            assert pp.is_capability_record(rid)

    def test_other_atlas_records_are_not_capabilities(self):
        for rid in ("atlas/project-deep:dco", "atlas/project:dco", "atlas/seams/x.md",
                    "atlas/approaches/internal/y.md", "haupt-wiki/skill:tdd", "", None):
            assert not pp.is_capability_record(rid)

    def test_select_keeps_order_limit_and_exclude(self):
        results = [_r("atlas/project-deep:dco"), _r("atlas/skill:a"), _r("atlas/seams/s.md"),
                   _r("atlas/skill:b"), _r("atlas/plugin:c"), _r("atlas/skill:d")]
        hits = pp.select_capability_hits(results, limit=3, exclude={"atlas/skill:b"})
        assert [h["record_id"] for h in hits] == ["atlas/skill:a", "atlas/plugin:c", "atlas/skill:d"]

    def test_select_survives_garbage(self):
        assert pp.select_capability_hits(None) == []
        assert pp.select_capability_hits([None, "x", {"record_id": None}]) == []


class TestProjectScopes:
    def test_both_spellings_for_underscore_folder(self):
        scopes = pp.project_scopes(r"C:\Users\alice\dynamic_central_orchestrator")
        assert scopes == ["project:dynamic_central_orchestrator",
                          "project:dynamic-central-orchestrator"]

    def test_case_is_kept_and_normalised(self):
        assert pp.project_scopes(r"C:\Users\alice\Desktop\OpenClaw") == \
            ["project:OpenClaw", "project:openclaw"]

    def test_single_spelling_when_identical(self):
        assert pp.project_scopes(r"C:\Users\alice\AI\Hooks-bau\prompt-prelude") == \
            ["project:prompt-prelude"]

    def test_generic_folders_have_no_scope(self):
        assert pp.project_scopes(r"C:\Users\alice\Desktop") == []
        assert pp.project_scopes(r"C:\Users\alice") == []
        assert pp.project_scopes(None) == []

    def test_deep_code_folder_maps_to_project(self):
        assert pp.project_scopes(r"C:\Users\alice\Desktop\dome-dynamics\src") == \
            ["project:dome-dynamics"]

    def test_agrees_with_project_slugs(self):
        for cwd in (r"C:\Users\alice\AI\agent-memory-atlas", r"C:\Users\alice\Desktop\OpenClaw",
                    r"C:\Users\alice\AI\Hooks-bau\prompt-prelude\replay"):
            slug = pp.project_slugs(cwd)[0]
            assert pp.project_scopes(cwd)[-1] == f"project:{slug}"


class TestProjectPot:
    def test_round_robin_across_spellings(self):
        a = [_r("am/L1"), _r("am/L2"), _r("am/L3")]
        b = [_r("atlas/project-deep:x"), _r("atlas/project:x")]
        hits = pp.select_project_pot([a, b], limit=2)
        assert [h["record_id"] for h in hits] == ["am/L1", "atlas/project-deep:x"]

    def test_single_spelling_gives_its_best(self):
        hits = pp.select_project_pot([[_r("am/L1"), _r("am/L2"), _r("am/L3")], None], limit=2)
        assert [h["record_id"] for h in hits] == ["am/L1", "am/L2"]

    def test_exclude_and_duplicates(self):
        a = [_r("am/L1"), _r("am/L2")]
        b = [_r("am/L1"), _r("am/L4")]
        hits = pp.select_project_pot([a, b], limit=3, exclude={"am/L2"})
        assert [h["record_id"] for h in hits] == ["am/L1", "am/L4"]

    def test_empty(self):
        assert pp.select_project_pot([], limit=2) == []
        assert pp.select_project_pot([None, []], limit=2) == []


class _Http:
    """Fake-Daemon: /search je scope, /classify mit festen Kosinus-Werten."""

    def __init__(self, by_scope, cosine=None, fail_classify=False):
        self.by_scope, self.cosine, self.fail_classify = by_scope, cosine or {}, fail_classify
        self.bodies = []

    def __call__(self, url, body, timeout):
        self.bodies.append((url.rsplit("/", 1)[-1], dict(body)))
        if url.endswith("/classify"):
            if self.fail_classify:
                raise ConnectionError("down")
            return {"scores": [{"name": l["name"], "score": self.cosine.get(l["name"], 0.0)}
                               for l in body["labels"]]}
        return {"results": self.by_scope.get(body.get("scope"), [])}


class TestScopedSearch:
    def test_body_carries_scope(self):
        http = _Http({"project:atlas": [_r("atlas/skill:a")]})
        res = pp.search_scoped_via_daemon("wort eins", "project:atlas", 7, http_fn=http)
        assert [r["record_id"] for r in res] == ["atlas/skill:a"]
        assert http.bodies == [("search", {"query": "wort eins", "k": 7, "scope": "project:atlas"})]

    def test_any_error_is_none(self):
        def boom(url, body, timeout):
            raise TimeoutError()
        assert pp.search_scoped_via_daemon("x", "project:atlas", http_fn=boom) is None
        assert pp.search_scoped_via_daemon("x", "project:atlas",
                                           http_fn=lambda *a: {"nope": 1}) is None


class TestCapabilityPot:
    def _http(self, **kw):
        return _Http({"project:atlas": [_r("atlas/project:p", "dossier"),
                                        _r("atlas/skill:a", "skill a", "Skill A"),
                                        _r("atlas/skill:b", "skill b", "Skill B")]}, **kw)

    def test_gate_keeps_only_cards_above_tau(self):
        http = self._http(cosine={"atlas/skill:a": 0.5, "atlas/skill:b": 0.2})
        hints, source = pp.lookup_capability_pot("prompt text", "wort eins", pp.DaemonBudget(),
                                                 0.4, http_fn=http)
        assert hints == ["atlas/skill:a — Skill A"] and source == "daemon"

    def test_without_classify_answer_no_caps(self):
        http = self._http(fail_classify=True)
        hints, source = pp.lookup_capability_pot("prompt text", "wort eins", pp.DaemonBudget(),
                                                 0.0, http_fn=http)
        assert hints == [] and source == "none"

    def test_exhausted_budget_gives_nothing(self):
        http = self._http(cosine={"atlas/skill:a": 0.9})
        budget = pp.DaemonBudget(budget_s=0.0)
        assert pp.lookup_capability_pot("p", "wort eins", budget, 0.1, http_fn=http) == ([], "none")
        assert http.bodies == []

    def test_empty_terms_do_not_search(self):
        http = self._http()
        assert pp.lookup_capability_pot("p", "  ", pp.DaemonBudget(), 0.1, http_fn=http) == ([], "none")
        assert http.bodies == []


class TestProjectPotLookup:
    def test_searches_each_spelling_and_merges(self):
        http = _Http({"project:my_proj": [_r("am/L1", "eins"), _r("am/L2", "zwei")],
                      "project:my-proj": [_r("atlas/project-deep:my-proj", "karte")]})
        items, source = pp.lookup_project_pot("wort eins", r"C:\Users\alice\my_proj",
                                              pp.DaemonBudget(), http_fn=http)
        assert [i["rid"] for i in items] == ["am/L1", "atlas/project-deep:my-proj"]
        assert items[0]["snippet"] == "eins" and source == "daemon"
        assert [b[1]["scope"] for b in http.bodies] == ["project:my_proj", "project:my-proj"]

    def test_no_project_no_search(self):
        http = _Http({})
        assert pp.lookup_project_pot("wort", r"C:\Users\alice\Desktop", pp.DaemonBudget(),
                                     http_fn=http) == ([], "none")
        assert http.bodies == []
