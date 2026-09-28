# -*- coding: utf-8 -*-
"""Blinder Relevanz-Judge (docs/2026-09-28-plan-relevanz-replay.md):
Paare poolen, Varianten verbergen, Verdikte validieren, Metriken rechnen."""
import json

import pytest

from replay import judge as jd

CORPUS = {"p1": {"pid": "p1", "prompt": "baue den runner um", "cwd": r"C:\x\evolab",
                 "prev_assistant": "A" * 900 + "ENDE"},
          "p2": {"pid": "p2", "prompt": "prüfe die telemetrie", "cwd": "", "prev_assistant": ""},
          "p3": {"pid": "p3", "prompt": "farbe gefällt mir nicht", "cwd": "", "prev_assistant": ""}}


def _item(rid, part="caps"):
    return {"rid": rid, "hint": rid + " — Titel", "snippet": "inhalt " + rid, "partition": part}


RUNS = {
    "V0": [{"pid": "p1", "gate": "pass", "items": [_item("a"), _item("b")]},
           {"pid": "p2", "gate": "pass", "items": [_item("c")]},
           {"pid": "p3", "gate": "no_work_signal", "items": []}],
    "V1": [{"pid": "p1", "gate": "pass", "items": [_item("a"), _item("b"), _item("d", "project")]},
           {"pid": "p2", "gate": "pass", "items": [_item("c")]},
           {"pid": "p3", "gate": "no_work_signal", "items": [_item("e", "project")]}],
}


class TestCollectPairs:
    def test_pairs_deduped_across_variants_and_blind(self):
        pairs = jd.collect_pairs(RUNS, CORPUS)
        assert len(pairs) == 5  # (p1,a) (p1,b) (p2,c) (p1,d) (p3,e)
        one = pairs[jd.pair_id("p1", "d")]
        assert one["prompt"] == "baue den runner um" and one["hint"] == "d — Titel"
        # blind: keine Varianten- oder Partitions-Info im Judge-Material
        assert "variant" not in one and "partition" not in one
        assert one["context"].endswith("ENDE") and len(one["context"]) == jd.CONTEXT_CAP

    def test_unknown_pid_is_skipped(self):
        runs = {"V0": [{"pid": "zz", "gate": "pass", "items": [_item("x")]}]}
        assert jd.collect_pairs(runs, CORPUS) == {}


class TestBatches:
    def test_shuffled_deterministic_excludes_cached(self):
        pairs = jd.collect_pairs(RUNS, CORPUS)
        cached = {jd.pair_id("p1", "a")}
        b1 = jd.make_batches(pairs, cached, batch_size=2, seed=3)
        b2 = jd.make_batches(pairs, cached, batch_size=2, seed=3)
        assert b1 == b2
        ids = [p["pair_id"] for b in b1 for p in b]
        assert len(ids) == 4 and jd.pair_id("p1", "a") not in ids
        assert [len(b) for b in b1] == [2, 2]


class TestVerdicts:
    def test_valid_verdicts_loaded(self, tmp_path):
        f = tmp_path / "verdicts_01.jsonl"
        f.write_text(json.dumps({"pair_id": "abc", "score": 2, "why": "x"}) + "\n"
                     + json.dumps({"pair_id": "def", "score": 0}) + "\n", encoding="utf-8")
        v, bad = jd.load_verdicts([f], known={"abc", "def"})
        assert v == {"abc": 2, "def": 0} and bad == []

    def test_invalid_rows_reported_not_silently_dropped(self, tmp_path):
        f = tmp_path / "verdicts_01.jsonl"
        f.write_text("kaputt\n"
                     + json.dumps({"pair_id": "abc", "score": 5}) + "\n"
                     + json.dumps({"pair_id": "zzz", "score": 1}) + "\n"
                     + json.dumps({"pair_id": "abc", "score": "2"}) + "\n", encoding="utf-8")
        v, bad = jd.load_verdicts([f], known={"abc"})
        assert v == {} and len(bad) == 4


class TestScore:
    def _verdicts(self, **scores):
        return {jd.pair_id(pid, rid): s for (pid, rid), s in
                {tuple(k.split("_")): v for k, v in scores.items()}.items()}

    def test_metrics_per_variant_and_gate(self):
        v = self._verdicts(p1_a=0, p1_b=1, p2_c=2, p1_d=2, p3_e=0)
        m = jd.score_runs(RUNS, v)
        v0, v1 = m["V0"]["pass"], m["V1"]["pass"]
        assert v0["n"] == 2 and v0["material_rate"] == 1.0
        assert v0["useful_rate"] == 0.5          # nur p2 hat ein 2er-Item
        assert v0["noise_rate"] == 0.0           # p1 hat b=1 -> kein reines Rauschen
        assert v0["precision_strict"] == pytest.approx(1 / 3)
        assert v1["useful_rate"] == 1.0          # d=2 macht p1 nützlich
        assert m["V1"]["no_work_signal"]["noise_rate"] == 1.0  # p3 nur e=0
        assert m["V0"]["no_work_signal"]["material_rate"] == 0.0
        assert v1["unjudged"] == 0

    def test_unjudged_items_are_counted(self):
        m = jd.score_runs(RUNS, {})
        assert m["V0"]["pass"]["unjudged"] == 3


class TestCriterion:
    def test_preregistered_rule(self):
        base = {"useful_rate": 0.30, "noise_rate": 0.20}
        assert jd.passes_criterion({"useful_rate": 0.40, "noise_rate": 0.25}, base)
        assert not jd.passes_criterion({"useful_rate": 0.39, "noise_rate": 0.20}, base)
        assert not jd.passes_criterion({"useful_rate": 0.50, "noise_rate": 0.26}, base)
