# -*- coding: utf-8 -*-
"""Runde 5: Gate-Runner — Kandidaten-Pool, Prüfung der Gate-Antworten, Lauf G."""
from replay import round5 as r5

LONG = "bitte implementiere die neue Funktion im Modul parser.py und teste sie"


def _e(pid="a", cwd=r"C:\Users\alice\my_proj", prompt=LONG, prev=""):
    return {"pid": pid, "prompt": prompt, "prev_assistant": prev, "cwd": cwd, "group": "start"}


def _r(rid, snippet="snippet text"):
    return {"record_id": rid, "snippet": snippet, "heading": rid.split("/")[-1]}


class _Search:
    def __init__(self, flat, scoped=None):
        self.flat, self.scoped, self.calls = flat, scoped or {}, []

    def __call__(self, terms, scope, k):
        self.calls.append(scope)
        return self.flat if scope is None else self.scoped.get(scope, [])


class TestPool:
    def test_union_of_flat_caps_and_project_without_duplicates(self):
        search = _Search(
            [_r("docs/x.md"), _r("atlas/skill:a")],
            {"project:atlas": [_r("atlas/project:p"), _r("atlas/skill:a"), _r("atlas/skill:b")],
             "project:my_proj": [_r("am/L1"), _r("docs/x.md")],
             "project:my-proj": [_r("atlas/project-deep:my-proj")]})
        pool = r5.build_pool(_e(), search)
        assert [(c["rid"], c["source"]) for c in pool] == [
            ("docs/x.md", "flat"), ("atlas/skill:a", "flat"), ("atlas/skill:b", "caps"),
            ("am/L1", "project"), ("atlas/project-deep:my-proj", "project")]
        assert [c["cid"] for c in pool] == ["c01", "c02", "c03", "c04", "c05"]
        assert search.calls == [None, "project:atlas", "project:my_proj", "project:my-proj"]

    def test_caps_limited_and_only_capability_kinds(self):
        scoped = {"project:atlas": [_r(f"atlas/skill:s{i}") for i in range(9)]
                  + [_r("atlas/seams/x.md")]}
        pool = r5.build_pool(_e(cwd=r"C:\Users\alice\Desktop"), _Search([], scoped))
        assert len(pool) == r5.CAP_TOP and all(c["source"] == "caps" for c in pool)

    def test_pool_is_capped(self):
        flat = [_r(f"docs/{i}.md") for i in range(12)]
        scoped = {"project:atlas": [_r(f"atlas/skill:s{i}") for i in range(12)],
                  "project:my_proj": [_r(f"am/L{i}") for i in range(5)],
                  "project:my-proj": [_r(f"pd/{i}") for i in range(5)]}
        pool = r5.build_pool(_e(), _Search(flat, scoped))
        assert len(pool) == min(r5.POOL_CAP, 12 + r5.CAP_TOP + 10)

    def test_empty_terms_no_search(self):
        search = _Search([_r("docs/x.md")])
        assert r5.build_pool(_e(prompt="ok"), search) == [] and search.calls == []

    def test_failed_search_is_survived(self):
        assert r5.build_pool(_e(), lambda terms, scope, k: None) == []


def test_gate_row_hides_rids_and_sources():
    pool = [{"cid": "c01", "rid": "docs/x.md", "hint": "docs/x.md — X", "snippet": "s" * 400,
             "source": "flat"}]
    row = r5.gate_row(_e(prev="antwort " * 200), pool)
    assert set(row) == {"pid", "prompt", "context", "cwd", "candidates"}
    assert set(row["candidates"][0]) == {"cid", "hint", "snippet"}
    assert len(row["candidates"][0]["snippet"]) == r5.GATE_SNIPPET_CAP
    assert len(row["context"]) <= 600


POOLS = {"a": [{"cid": "c01", "rid": "x/1", "hint": "x/1 — T", "snippet": "s1", "source": "flat"},
               {"cid": "c02", "rid": "x/2", "hint": "x/2 — T", "snippet": "s2", "source": "caps"},
               {"cid": "c03", "rid": "x/3", "hint": "x/3 — T", "snippet": "s3", "source": "project"}],
         "b": []}


class TestKeeps:
    def test_valid_answers(self):
        keeps, problems = r5.load_keeps(
            ['{"pid": "a", "keep": ["c02"], "why": "passt"}', '{"pid": "b", "keep": []}', ""], POOLS)
        assert keeps == {"a": ["c02"], "b": []} and problems == []

    def test_problems_are_reported_not_hidden(self):
        keeps, problems = r5.load_keeps([
            "kein json",
            '{"pid": "zzz", "keep": []}',
            '{"pid": "a", "keep": ["c01", "c09", "c02", "c03"]}',
            '{"pid": "a", "keep": []}',
            '{"pid": "b", "keep": "c01"}'], POOLS)
        assert keeps == {"a": ["c01", "c02"]}
        text = " | ".join(problems)
        for needle in ("unlesbar", "unbekannte pid", "unbekannte cid", "mehr als 2", "doppelte pid"):
            assert needle in text

    def test_gate_run_maps_cids_back_to_records(self):
        rows = r5.gate_run([_e("a"), _e("b"), _e("c")], POOLS, {"a": ["c03", "c01"], "b": []})
        assert [(i["rid"], i["partition"]) for i in rows[0]["items"]] == \
            [("x/3", "project"), ("x/1", "flat")]
        assert rows[1]["items"] == [] and rows[2]["items"] == []
        assert rows[0]["group"] == "start"


def test_quiet_criterion():
    base = {"useful_rate": 0.04, "noise_rate": 0.26}
    assert r5.quiet_criterion({"useful_rate": 0.04, "noise_rate": 0.13}, base)
    assert not r5.quiet_criterion({"useful_rate": 0.03, "noise_rate": 0.05}, base)
    assert not r5.quiet_criterion({"useful_rate": 0.10, "noise_rate": 0.14}, base)
