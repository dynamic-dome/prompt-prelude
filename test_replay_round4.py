# -*- coding: utf-8 -*-
"""Runde 4: Replay-Runner — Testmenge, Varianten-Logik, Paar-Schlüssel, τ-Regel."""
import json

import pytest

from replay import round4 as r4

LONG = "bitte implementiere die neue Funktion im Modul parser.py und teste sie"


def _e(pid, prompt=LONG, prev="", cwd=r"C:\Users\alice\my_proj"):
    return {"pid": pid, "prompt": prompt, "prev_assistant": prev, "cwd": cwd}


def _r(rid, snippet="snippet text"):
    return {"record_id": rid, "snippet": snippet, "heading": rid.split("/")[-1]}


class TestTestSet:
    def test_gate_pass_and_session_start_bypass(self):
        entries = [
            _e("a"),                                               # Start, Gate pass
            _e("b", prev="antwort"),                               # Fortsetzung, Gate pass
            _e("c", prompt="was meinst du denn dazu so ganz allgemein eigentlich heute"),  # Start, kein Work-Signal
            _e("d", prompt="was meinst du denn dazu so ganz allgemein eigentlich heute", prev="x"),
            _e("e", prompt="ok"),                                  # trivial
            _e("f"),                                               # schon benutzt
        ]
        got = {e["pid"]: e["group"] for e in r4.test_set(entries, used={"f"})}
        assert got == {"a": "start", "b": "cont", "c": "start"}


class _Search:
    def __init__(self, flat, scoped=None, project_flat=None):
        self.flat, self.scoped, self.project_flat = flat, scoped or {}, project_flat
        self.calls = []

    def __call__(self, terms, scope, k):
        self.calls.append((scope, k))
        if scope is not None:
            return self.scoped.get(scope, [])
        if self.project_flat is not None and "proj" in terms.split():
            return self.project_flat
        return self.flat


FLAT = [_r("atlas/project-deep:other"), _r("docs/x.md"), _r("atlas/skill:a")]


class TestFlatVariants:
    def test_v0_start_has_caps_and_project_card(self):
        search = _Search(FLAT, project_flat=[_r("agent-memory/my-proj/learnings/L1")])
        out = r4.items_flat(dict(_e("a"), group="start"), search, everywhere=False)
        parts = [(i["partition"], i["rid"]) for i in out["items"]]
        assert ("caps", "atlas/project-deep:other") in parts and ("caps", "atlas/skill:a") in parts
        assert ("project", "agent-memory/my-proj/learnings/L1") in parts

    def test_v0_continuation_suppresses_caps_and_project(self):
        search = _Search(FLAT, project_flat=[_r("agent-memory/my-proj/learnings/L1")])
        out = r4.items_flat(dict(_e("a", prev="x"), group="cont"), search, everywhere=False)
        assert [i for i in out["items"] if i["partition"] in ("caps", "project")] == []

    def test_v11_keeps_caps_at_continuation(self):
        out = r4.items_flat(dict(_e("a", prev="x"), group="cont"), _Search(FLAT), everywhere=True)
        assert [i["rid"] for i in out["items"] if i["partition"] == "caps"] == \
            ["atlas/project-deep:other", "atlas/skill:a"]


class TestPotVariants:
    def _search(self):
        return _Search(FLAT, scoped={
            "project:atlas": [_r("atlas/project:p"), _r("atlas/skill:a"), _r("atlas/skill:b")],
            "project:my_proj": [_r("am/L1"), _r("am/L2")],
            "project:my-proj": [_r("atlas/project-deep:my-proj")],
        })

    def test_b_start_uses_pots_and_gate(self):
        classify = lambda prompt, items: {"atlas/skill:a": 0.6, "atlas/skill:b": 0.1}
        out = r4.items_pots(dict(_e("a"), group="start"), self._search(), classify, 0.4, False)
        parts = [(i["partition"], i["rid"]) for i in out["items"]]
        assert parts == [("caps", "atlas/skill:a"), ("project", "am/L1"),
                         ("project", "atlas/project-deep:my-proj")]
        assert {c["rid"]: c["cos"] for c in out["caps_scored"]} == \
            {"atlas/skill:a": 0.6, "atlas/skill:b": 0.1}

    def test_b_never_takes_foreign_dossiers_as_caps(self):
        classify = lambda prompt, items: {i["rid"]: 0.9 for i in items}
        out = r4.items_pots(dict(_e("a"), group="start"), self._search(), classify, 0.1, False)
        assert all(i["rid"].startswith("atlas/skill:")
                   for i in out["items"] if i["partition"] == "caps")

    def test_b_continuation_is_mentors_only(self):
        search = self._search()
        out = r4.items_pots(dict(_e("a", prev="x"), group="cont"), search, None, 0.4, False)
        assert out["items"] == [] and [c[0] for c in search.calls] == [None]

    def test_bplus_continuation_uses_pots(self):
        classify = lambda prompt, items: {"atlas/skill:a": 0.6}
        out = r4.items_pots(dict(_e("a", prev="x"), group="cont"), self._search(), classify, 0.4, True)
        assert [i["partition"] for i in out["items"]] == ["caps", "project", "project"]

    def test_failed_classify_means_no_caps(self):
        out = r4.items_pots(dict(_e("a"), group="start"), self._search(), lambda p, i: None, 0.0, False)
        assert [i["partition"] for i in out["items"]] == ["project", "project"]


class TestPairsAndScores:
    def test_key_changes_with_snippet_but_not_whitespace(self):
        assert r4.pair_key("p", "r", "ein  text") == r4.pair_key("p", "r", "ein text")
        assert r4.pair_key("p", "r", "ein text") != r4.pair_key("p", "r", "anderer text")
        assert r4.pair_key("p", "r", "x") != r4.pair_key("p", "r2", "x")

    def test_pairs_are_pooled_without_variant_info(self):
        corpus = {"a": _e("a")}
        item = {"rid": "x/1", "hint": "x/1 — T", "snippet": "s", "partition": "caps"}
        runs = {"I0__V0": [{"pid": "a", "group": "start", "items": [item]}],
                "I0__B": [{"pid": "a", "group": "start", "items": [item]}]}
        pairs = r4.collect_pairs(runs, corpus)
        assert len(pairs) == 1
        assert set(next(iter(pairs.values()))) == {"pair_id", "prompt", "context", "cwd", "hint", "snippet"}

    def test_rates_use_all_prompts_of_group(self):
        def row(pid, group, rids):
            return {"pid": pid, "group": group,
                    "items": [{"rid": r, "snippet": "s", "partition": "caps"} for r in rids]}
        rows = [row("a", "start", ["u"]), row("b", "start", ["n"]), row("c", "start", []),
                row("d", "cont", ["u"])]
        verdicts = {r4.pair_key("a", "u", "s"): 2, r4.pair_key("b", "n", "s"): 0,
                    r4.pair_key("d", "u", "s"): 2}
        m = r4.score_rows(rows, verdicts, "start")
        assert m["n"] == 3 and m["useful_rate"] == pytest.approx(1 / 3)
        assert m["noise_rate"] == pytest.approx(1 / 3) and m["material_rate"] == pytest.approx(2 / 3)
        assert r4.score_rows(rows, verdicts)["n"] == 4

    def test_criterion(self):
        base = {"useful_rate": 0.20, "noise_rate": 0.20}
        assert r4.passes({"useful_rate": 0.30, "noise_rate": 0.25}, base)
        assert not r4.passes({"useful_rate": 0.29, "noise_rate": 0.20}, base)
        assert not r4.passes({"useful_rate": 0.40, "noise_rate": 0.26}, base)


class TestChooseTau:
    def test_smallest_decile_meeting_precision(self):
        # 20 Paare: niedrige Kosinus-Werte irrelevant, hohe hilfreich
        scored = [(i / 20, 2 if i >= 14 else 0) for i in range(20)]
        tau, grid = r4.choose_tau(scored)
        chosen = next(g for g in grid if g["tau"] == tau)
        assert chosen["strict"] >= r4.TAU_MIN_STRICT and chosen["kept_share"] >= r4.TAU_MIN_KEPT
        assert all(g["strict"] < r4.TAU_MIN_STRICT for g in grid if g["tau"] < tau)

    def test_fallback_is_best_precision_with_enough_kept(self):
        scored = [(i / 20, 2 if i == 19 else 0) for i in range(20)]   # nie 35 %
        tau, grid = r4.choose_tau(scored)
        eligible = [g for g in grid if g["kept_share"] >= r4.TAU_MIN_KEPT]
        assert next(g for g in grid if g["tau"] == tau)["strict"] == max(g["strict"] for g in eligible)

    def test_empty_raises(self):
        with pytest.raises(ValueError):
            r4.choose_tau([])


def test_old_caps_pairs_reads_only_judged_caps(tmp_path):
    from replay import judge as jd
    (tmp_path / "runs").mkdir()
    (tmp_path / "judge").mkdir()
    row = {"pid": "a", "items": [{"rid": "atlas/skill:x", "snippet": "s", "partition": "caps"},
                                 {"rid": "wiki/y", "snippet": "t", "partition": "mentor"},
                                 {"rid": "atlas/skill:z", "snippet": "u", "partition": "caps"}]}
    (tmp_path / "runs" / "V0.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")
    (tmp_path / "judge" / "verdicts_01.jsonl").write_text(
        json.dumps({"pair_id": jd.pair_id("a", "atlas/skill:x"), "score": 2}) + "\n" +
        json.dumps({"pair_id": jd.pair_id("a", "wiki/y"), "score": 1}) + "\n", encoding="utf-8")
    assert r4.old_caps_pairs(tmp_path) == [("a", "atlas/skill:x", "s", 2)]
