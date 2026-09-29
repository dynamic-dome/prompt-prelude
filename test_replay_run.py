# -*- coding: utf-8 -*-
"""Replay-Runner: Varianten V0-V3 gegen einen festen Korpus
(docs/2026-09-28-plan-relevanz-replay.md). Die Suche ist injiziert — kein
Test trifft den echten Daemon."""
from replay import run as rr

ENTRY = {"pid": "p1", "prompt": "baue den lighthouse runner für das dashboard um",
         "cwd": r"C:\Users\alice\AI\evolab",
         "prev_assistant": "Der Mutator ist fertig. Der Mutator schreibt Kandidaten, "
                           "Lighthouse bewertet die Kandidaten."}

RESULTS = [
    {"record_id": "atlas/skill:frontend-design", "heading": "Frontend Design", "snippet": "ui"},
    {"record_id": "haupt-wiki/queries/2026-07-01-session-runner-dashboard.md",
     "heading": "Runner Dashboard", "snippet": "lighthouse runner dashboard umgebaut"},
    {"record_id": "atlas/project-deep:evolab", "heading": "evolab", "snippet": "optimierer"},
    {"record_id": "atlas/project:crazy-professor", "heading": "Crazy", "snippet": "ideen"},
    {"record_id": "atlas/skill:session-summary", "heading": "Summary", "snippet": "notes"},
]


class TestVariantQuery:
    def test_v0_is_the_hook_query(self):
        assert rr.variant_query(ENTRY, context=False) == \
            "baue lighthouse runner dashboard"

    def test_context_appends_answer_terms(self):
        q = rr.variant_query(ENTRY, context=True)
        assert q.startswith("baue lighthouse runner dashboard ")
        assert "mutator" in q.split() and "kandidaten" in q.split()


class TestSelectItems:
    def test_baseline_partitions_like_the_hook(self):
        items = rr.select_items(RESULTS, "lighthouse runner dashboard", slugs=[])
        caps = [i["rid"] for i in items if i["partition"] == "caps"]
        mentors = [i["rid"] for i in items if i["partition"] == "mentor"]
        assert caps == ["atlas/skill:frontend-design", "atlas/project-deep:evolab",
                        "atlas/project:crazy-professor"]
        assert mentors == ["haupt-wiki/queries/2026-07-01-session-runner-dashboard.md"]
        assert all(i["hint"] and "snippet" in i for i in items)

    def test_project_partition_adds_only_new_slug_hits(self):
        project_results = [
            {"record_id": "atlas/project-deep:evolab", "heading": "evolab"},  # schon in caps
            {"record_id": "agent-memory/evolab/learnings/L13", "heading": "Chrome-Falle",
             "snippet": "chrome version"},
            {"record_id": "atlas/skill:unrelated", "heading": "x"}]
        items = rr.select_items(RESULTS, "lighthouse runner dashboard", slugs=["evolab"],
                                project_results=project_results)
        proj = [i["rid"] for i in items if i["partition"] == "project"]
        assert proj == ["agent-memory/evolab/learnings/L13"]
        assert items[-1]["snippet"] == "chrome version"

    def test_no_slugs_no_project_partition(self):
        items = rr.select_items(RESULTS, "lighthouse runner dashboard", slugs=[],
                                project_results=RESULTS)
        assert not [i for i in items if i["partition"] == "project"]

    def test_empty_results(self):
        assert rr.select_items([], "x y", slugs=[]) == []
        assert rr.select_items(None, "x y", slugs=[]) == []


class TestGateAndSample:
    def test_gate_mirrors_hook(self):
        assert rr.gate_of("ok") == "trivial"
        assert rr.gate_of("zu kurz") == "too_short"
        assert rr.gate_of("baue einen neuen parser für die telemetrie-datei") == "pass"
        assert rr.gate_of("hört sich alles gut an, aber die farbe gefällt mir noch nicht so") \
            == "no_work_signal"

    def test_stratified_sample_is_deterministic(self):
        entries = ([{"pid": f"a{i}", "prompt": "baue einen neuen parser für modul %d" % i}
                    for i in range(10)]
                   + [{"pid": f"b{i}", "prompt": "hört sich gut an aber die farbe passt "
                                                 "noch nicht so ganz %d" % i}
                      for i in range(10)])
        s1 = rr.stratified_sample(entries, n_pass=4, n_nws=3, seed=7)
        s2 = rr.stratified_sample(entries, n_pass=4, n_nws=3, seed=7)
        assert [e["pid"] for e in s1] == [e["pid"] for e in s2]
        assert sum(1 for e in s1 if e["gate"] == "pass") == 4
        assert sum(1 for e in s1 if e["gate"] == "no_work_signal") == 3


class TestHoldoutSessionStarts:
    def test_only_untouched_first_prompts_with_substance(self):
        long_nws = "hört sich gut an aber die farbe passt noch nicht so ganz "
        entries = [
            {"pid": "used", "prompt": long_nws + "1", "prev_assistant": ""},
            {"pid": "start", "prompt": long_nws + "2", "prev_assistant": ""},
            {"pid": "cont", "prompt": long_nws + "3", "prev_assistant": "vorher"},
            {"pid": "short", "prompt": "ok", "prev_assistant": ""},
            {"pid": "work", "prompt": "baue einen neuen parser für die telemetrie", "prev_assistant": ""},
        ]
        out = rr.holdout_session_starts(entries, used_pids={"used"})
        assert [e["pid"] for e in out] == ["start", "work"]
        assert {e["gate"] for e in out} == {"no_work_signal", "pass"}


class TestRunReplay:
    def test_variants_share_search_cache(self):
        calls = []

        def fake_search(terms):
            calls.append(terms)
            return RESULTS

        entries = [dict(ENTRY, gate="pass")]
        out = rr.run_replay(entries, rr.VARIANTS, fake_search)
        assert set(out) == {"V0", "V1", "V2", "V3"}
        # Basis-Queries: V0/V1 teilen eine, V2/V3 eine; dazu je eine
        # Projekt-Query für V1 und V3 -> genau 4 Suchen
        assert len(calls) == 4
        assert "baue lighthouse runner dashboard evolab" in calls
        v1 = out["V1"][0]
        assert v1["slugs"] == ["evolab"] and v1["gate"] == "pass"
        # V0 trägt keine Projekt-Partition; V1 nur Slug-Treffer, die nicht
        # schon als Caps drin sind (hier: keine neuen -> identisch zu V0)
        assert not [i for i in out["V0"][0]["items"] if i["partition"] == "project"]
        assert [i["rid"] for i in v1["items"]] == [i["rid"] for i in out["V0"][0]["items"]]

    def test_search_failure_yields_empty_items(self):
        out = rr.run_replay([dict(ENTRY, gate="pass")], {"V0": rr.VARIANTS["V0"]},
                            lambda terms: None)
        assert out["V0"][0]["items"] == [] and out["V0"][0]["search_ok"] is False
