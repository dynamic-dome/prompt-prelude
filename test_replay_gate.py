# -*- coding: utf-8 -*-
"""Runde 2 (docs/2026-09-28-plan-relevanz-replay.md): Item-Gate per
Kosinus-Ähnlichkeit Prompt x Treffer-Snippet über /classify. Split, Gate und
τ-Wahl sind vorregistriert; hier nur deren Mechanik."""
import pytest

import prompt_prelude as pp
from replay import gate as gt
from replay import judge as jd


class TestScoreItems:
    ITEMS = [{"rid": "atlas/skill:a", "snippet": "debugging von parsern"},
             {"rid": "agent-memory/x/learnings/L1", "snippet": "cp1252 falle beim stdin"},
             {"rid": "haupt-wiki/queries/leer.md", "snippet": ""}]

    def test_posts_prompt_and_snippets_to_classify(self):
        seen = {}

        def fn(url, body, timeout):
            seen.update(url=url, body=body, timeout=timeout)
            return {"scores": [{"name": "agent-memory/x/learnings/L1", "score": 0.61},
                               {"name": "atlas/skill:a", "score": 0.22}]}

        out = pp.score_items("x" * 900, self.ITEMS, http_fn=fn, timeout=3.0)
        assert out == {"agent-memory/x/learnings/L1": 0.61, "atlas/skill:a": 0.22}
        assert seen["url"].endswith("/classify") and seen["timeout"] == 3.0
        assert len(seen["body"]["query"]) == pp.CLASSIFY_PROMPT_CAP
        # Treffer ohne Snippet haben nichts zu vergleichen -> nicht mitgeschickt
        assert [l["name"] for l in seen["body"]["labels"]] == \
            ["atlas/skill:a", "agent-memory/x/learnings/L1"]

    def test_failure_returns_none(self):
        def boom(url, body, timeout):
            raise ConnectionError("down")

        assert pp.score_items("prompt", self.ITEMS, http_fn=boom) is None

    def test_nothing_to_score_skips_the_call(self):
        def never(url, body, timeout):
            raise AssertionError("darf nicht aufgerufen werden")

        assert pp.score_items("prompt", [{"rid": "a", "snippet": ""}], http_fn=never) == {}
        assert pp.score_items("", self.ITEMS, http_fn=never) == {}


class TestGateItems:
    def test_keeps_only_items_at_or_above_tau(self):
        items = [{"rid": "a"}, {"rid": "b"}, {"rid": "c"}]
        kept = pp.gate_items(items, {"a": 0.50, "b": 0.30}, tau=0.30)
        assert [i["rid"] for i in kept] == ["a", "b"]  # c ohne Score fällt raus


class TestSplit:
    def test_half_is_deterministic_hash_parity(self):
        assert gt.half_of("p1") == gt.half_of("p1")
        halves = {gt.half_of(f"pid{i}") for i in range(20)}
        assert halves == {0, 1}


class TestApplyGate:
    def test_filters_rows_by_scores(self):
        rows = [{"pid": "p1", "gate": "pass", "items": [{"rid": "a"}, {"rid": "b"}]},
                {"pid": "p2", "gate": "pass", "items": [{"rid": "c"}]}]
        scores = {"p1": {"a": 0.6, "b": 0.1}, "p2": {"c": 0.2}}
        out = gt.apply_gate(rows, scores, tau=0.3)
        assert [[i["rid"] for i in r["items"]] for r in out] == [["a"], []]
        assert rows[0]["items"][1]["rid"] == "b"  # Original unverändert


class TestSelectTau:
    def _setup(self):
        # 4 Gate-Pass-Prompts; V0 hat je ein Item. p1: nützlich (2), p2-p4 Rauschen (0).
        v0 = [{"pid": f"p{i}", "gate": "pass", "items": [{"rid": f"x{i}"}]} for i in range(1, 5)]
        verdicts = {jd.pair_id(f"p{i}", f"x{i}"): (2 if i == 1 else 0) for i in range(1, 5)}
        scores = {"p1": {"x1": 0.55}, "p2": {"x2": 0.28}, "p3": {"x3": 0.33}, "p4": {"x4": 0.41}}
        return v0, verdicts, scores

    def test_smallest_tau_meeting_both_conditions(self):
        v0, verdicts, scores = self._setup()
        # V0: useful 25 %, noise 75 %. Ziel: noise <= 37.5 %, useful >= 23 %.
        # tau 0.35 -> p1 + p4 bleiben: noise 25 %, useful 25 % -> erstes gültiges
        tau = gt.select_tau(v0, v0, scores, verdicts, taus=[0.25, 0.30, 0.35, 0.40, 0.45])
        assert tau == pytest.approx(0.35)

    def test_fallback_lowest_noise_under_useful_condition(self):
        v0, verdicts, scores = self._setup()
        # Nur taus, die die Halbierung nicht schaffen: 0.25 (noise 75 %), 0.30 (50 %)
        tau = gt.select_tau(v0, v0, scores, verdicts, taus=[0.25, 0.30])
        assert tau == pytest.approx(0.30)
