# -*- coding: utf-8 -*-
"""Messbank: Faelle aus der Recon bauen (Prelude v2, Task B3).
Alle Eingaben sind erfundene Mini-Daten; nie die echten Recon-Artefakte."""
import json

from replay import bank

T0 = 1790578115.0  # 2026-09-28T06:48:35Z (UTC)


def _event(i, preview, **kw):
    ev = {"t": T0 + i * 100, "session": f"s{i}", "transcript": f"t{i}.jsonl",
          "preview": preview, "session_start": False, "cwd": f"C:\\proj\\p{i}"}
    ev.update(kw)
    return ev


def _row(i, prompt, **kw):
    row = {"session": f"s{i}", "uuid": f"u{i}", "ts": "2026-09-28T06:50:00.000Z",
           "cwd": f"C:\\proj\\p{i}", "prompt": prompt, "prev_assistant": f"antwort {i}"}
    row.update(kw)
    return row


def _case(cid, idx):
    return {"case_id": cid, "event_index": idx}


def _judgment(cid, verdicts, better="no", candidates=()):
    return {"case": cid, "better_missing": better, "candidates": list(candidates),
            "items": [{"record_id": r, "verdict": v} for r, v in verdicts]}


def _reader(table):
    return lambda path: table.get(path, [])


class TestBuildCases:
    def test_build_cases_maps_positives_negatives_and_expect_nothing(self):
        events = [_event(0, "alpha beta gamma delta eps"),
                  _event(1, "zweiter prompt vorschau!!", session_start=True),
                  _event(2, "dritter prompt vorschau!!")]
        sample = {"cases": [_case("C001", 0), _case("C002", 1), _case("C003", 2)]}
        judgments = [
            _judgment("C001", [("agent-memory/a/learnings/L1", "relevant"),
                               ("native-x/y", "irrelevant"),
                               ("atlas/skill:foo", "marginal"),
                               ("atlas/plugin:dup", "duplicate")],
                      better="yes", candidates=["agent-memory/b/learnings/L2"]),
            _judgment("C002", [("native-x/z", "irrelevant")], better="no"),
            _judgment("C003", [("native-q/r", "marginal")], better="already_in_static_context",
                      candidates=["native-q/static"]),
        ]
        prompts = {"t0.jsonl": [_row(0, "alpha beta gamma delta eps und mehr")],
                   "t1.jsonl": [_row(1, "zweiter prompt vorschau!! mit rest")],
                   "t2.jsonl": [_row(2, "dritter prompt vorschau!! mit rest")]}
        cases = bank.build_cases(events, sample, judgments, _reader(prompts))
        assert [c["case_id"] for c in cases] == ["C001", "C002", "C003"]
        c1, c2, c3 = cases
        assert c1 == {"case_id": "C001", "ts": "2026-09-28T06:48:35Z",
                      "cwd": "C:\\proj\\p0", "session_start": False,
                      "prompt": "alpha beta gamma delta eps und mehr",
                      "prev_answer": "antwort 0",
                      "positives": ["agent-memory/a/learnings/L1", "agent-memory/b/learnings/L2"],
                      "negatives": ["native-x/y"],
                      "expect_nothing": False, "already_static": False}
        assert c2["session_start"] is True
        assert c2["positives"] == [] and c2["negatives"] == ["native-x/z"]
        assert c2["expect_nothing"] is True and c2["already_static"] is False
        # bereits im statischen Kontext: nichts einspielen, Kandidat zaehlt nicht als Positiv
        assert c3["already_static"] is True and c3["expect_nothing"] is True
        assert c3["positives"] == []

    def test_relevant_hit_or_better_missing_blocks_expect_nothing(self):
        events = [_event(0, "aaaaaaaaaaaaaaaaaaaaaaaaa"), _event(1, "bbbbbbbbbbbbbbbbbbbbbbbbb"),
                  _event(2, "ccccccccccccccccccccccccc")]
        sample = {"cases": [_case("C1", 0), _case("C2", 1), _case("C3", 2)]}
        judgments = [_judgment("C1", [("native-a/b", "relevant")], better="no"),
                     _judgment("C2", [("native-a/c", "irrelevant")], better="yes",
                               candidates=["native-a/d"]),
                     _judgment("C3", [("native-a/e", "irrelevant")], better="uncertain")]
        prompts = {f"t{i}.jsonl": [_row(i, ch * 25)] for i, ch in enumerate("abc")}
        c1, c2, c3 = bank.build_cases(events, sample, judgments, _reader(prompts))
        assert c1["expect_nothing"] is False
        assert c2["expect_nothing"] is False
        assert c3["expect_nothing"] is False

    def test_rid_that_is_also_positive_is_not_a_negative(self):
        events = [_event(0, "x" * 25)]
        sample = {"cases": [_case("C1", 0)]}
        judgments = [_judgment("C1", [("native-a/b", "relevant"), ("native-a/b", "irrelevant")],
                               better="no")]
        (c,) = bank.build_cases(events, sample, judgments, _reader({"t0.jsonl": [_row(0, "x" * 30)]}))
        assert c["positives"] == ["native-a/b"] and c["negatives"] == []

    def test_candidate_suffix_is_stripped(self):
        events = [_event(0, "x" * 25)]
        sample = {"cases": [_case("C1", 0)]}
        cands = ["native-a/one (already in DCO MEMORY.md)", "atlas/skill:two (uncertain: card vf 10-06)",
                 "agent-memory/p/learnings/L3", "native-a/one"]
        judgments = [_judgment("C1", [], better="yes", candidates=cands)]
        (c,) = bank.build_cases(events, sample, judgments, _reader({"t0.jsonl": [_row(0, "x" * 30)]}))
        assert c["positives"] == ["native-a/one", "atlas/skill:two", "agent-memory/p/learnings/L3"]

    def test_case_without_original_prompt_is_skipped_and_counted(self):
        events = [_event(0, "x" * 25), _event(1, "y" * 25), _event(2, "z" * 25, transcript=None),
                  _event(3, "w" * 25)]
        sample = {"cases": [_case("C1", 0), _case("C2", 1), _case("C3", 2), _case("C4", 3)]}
        judgments = [_judgment(f"C{i}", [], better="no") for i in range(1, 5)]
        prompts = {
            "t0.jsonl": [_row(0, "x" * 30)],
            # anderer Prompt in der Sitzung -> Vorschau passt nicht
            "t1.jsonl": [_row(1, "ganz etwas anderes")],
            # Vorschau passt, aber Zeitabstand > 120 s
            "t3.jsonl": [_row(3, "w" * 30, ts="2026-09-28T10:00:00.000Z")],
        }
        skipped = []
        cases = bank.build_cases(events, sample, judgments, _reader(prompts), skipped=skipped)
        assert [c["case_id"] for c in cases] == ["C1"]
        assert skipped == ["C2", "C3", "C4"]

    def test_case_without_judgment_is_skipped_and_counted(self):
        events = [_event(0, "x" * 25)]
        sample = {"cases": [_case("C1", 0)]}
        skipped = []
        cases = bank.build_cases(events, sample, [], _reader({"t0.jsonl": [_row(0, "x" * 30)]}),
                                 skipped=skipped)
        assert cases == [] and skipped == ["C1"]

    def test_cwd_falls_back_to_event_when_transcript_row_has_none(self):
        events = [_event(0, "x" * 25)]
        sample = {"cases": [_case("C1", 0)]}
        judgments = [_judgment("C1", [], better="no")]
        (c,) = bank.build_cases(events, sample, judgments,
                                _reader({"t0.jsonl": [_row(0, "x" * 30, cwd=None)]}))
        assert c["cwd"] == "C:\\proj\\p0"

    def test_transcript_is_read_once_per_path(self):
        events = [_event(0, "x" * 25), _event(1, "x" * 25, transcript="t0.jsonl", session="s0")]
        sample = {"cases": [_case("C1", 0), _case("C2", 1)]}
        judgments = [_judgment("C1", [], better="no"), _judgment("C2", [], better="no")]
        calls = []

        def reader(path):
            calls.append(path)
            return [_row(0, "x" * 30)]

        assert len(bank.build_cases(events, sample, judgments, reader)) == 2
        assert calls == ["t0.jsonl"]


class TestCli:
    def test_build_writes_jsonl_and_reports_skipped(self, tmp_path, capsys):
        art = tmp_path / "recon"
        (art / "judge").mkdir(parents=True)
        tr = tmp_path / "s0.jsonl"
        tr.write_text(json.dumps({
            "type": "user", "uuid": "u0", "sessionId": "s0", "isSidechain": False,
            "timestamp": "2026-09-28T06:50:00.000Z", "cwd": "C:\\proj\\p0", "entrypoint": "cli",
            "origin": {"kind": "human"},
            "message": {"role": "user", "content": "x" * 30}}) + "\n", encoding="utf-8")
        events = [_event(0, "x" * 25, transcript=str(tr)), _event(1, "y" * 25)]
        (art / "events.json").write_text(json.dumps(events), encoding="utf-8")
        (art / "sample.json").write_text(
            json.dumps({"cases": [_case("C1", 0), _case("C2", 1)]}), encoding="utf-8")
        (art / "judge" / "judgments.json").write_text(
            json.dumps([_judgment("C1", [("native-a/b", "relevant")]),
                        _judgment("C2", [])]), encoding="utf-8")
        out = tmp_path / "bank" / "cases.jsonl"
        assert bank.main(["build", "--artifacts", str(art), "--out", str(out)]) == 0
        rows = [json.loads(line) for line in out.read_text(encoding="utf-8").splitlines()]
        assert [r["case_id"] for r in rows] == ["C1"]
        assert rows[0]["positives"] == ["native-a/b"] and rows[0]["prompt"] == "x" * 30
        msg = capsys.readouterr().out
        assert "1 Faelle" in msg and "uebersprungen: 1" in msg and "C2" in msg


# --- B4: Pools, Gate- und Gutachter-Pakete -----------------------------------

from replay import v2_contract as v2  # noqa: E402


def _hit(rid, **kw):
    hit = {"record_id": rid, "snippet": f"Auszug {rid}.", "summary": None,
           "superseded_by": None, "valid_from": "2026-09-01"}
    hit.update(kw)
    return hit


def _pool_case(**kw):
    case = {"case_id": "C1", "ts": "2026-09-28T06:48:35Z", "cwd": "C:\\proj\\p0", "session_start": False,
            "prompt": "Docker Compose Healthcheck schlaegt fehl", "prev_answer": "Der Container startet nicht.",
            "positives": ["agent-memory/a/learnings/L1"], "negatives": [], "expect_nothing": False,
            "already_static": False}
    case.update(kw)
    return case


class FakeSearch:
    """Zeichnet die Daemon-Bodies auf; antwortet je Topf aus `by_prefix` (erstes Praefix)."""

    def __init__(self, by_first_prefix=None):
        self.by_first_prefix = by_first_prefix or {}
        self.bodies = []

    def __call__(self, body):
        self.bodies.append(body)
        return list(self.by_first_prefix.get(body["record_id_prefix"][0], []))


class TestPools:
    def test_pools_use_prefix_list_and_as_of_per_pot(self):
        search = FakeSearch()
        case = _pool_case()
        bank.pools_for_case(case, search)
        assert [b["record_id_prefix"] for b in search.bodies] == [
            list(v2.POTS[pot]["prefixes"]) for pot in v2.POTS]
        for body, pot in zip(search.bodies, v2.POTS):
            assert body == {"query": v2.build_query(case["prompt"], case["prev_answer"]),
                            "k": v2.POTS[pot]["k"], "record_id_prefix": list(v2.POTS[pot]["prefixes"]),
                            "as_of": "2026-09-28T06:48:35Z", "caller": "prelude-bank"}

    def test_candidate_shape_and_cids_are_sequential_per_pot(self):
        search = FakeSearch({
            "agent-memory/": [_hit("agent-memory/a/L1", summary="Kern 1"), _hit("native-x/y")],
            "atlas/skill:": [_hit("atlas/skill:foo")],
            "atlas/mcp-server:": [_hit("atlas/plugin:bar"), _hit("atlas/mcp-server:baz")]})
        pools = bank.pools_for_case(_pool_case(), search)
        assert list(pools) == ["learnings", "skills", "capabilities"]
        assert [c["cid"] for c in pools["learnings"]] == ["l01", "l02"]
        assert [c["cid"] for c in pools["skills"]] == ["s01"]
        assert [c["cid"] for c in pools["capabilities"]] == ["c01", "c02"]
        assert pools["learnings"][0] == {"cid": "l01", "pot": "learnings", "rid": "agent-memory/a/L1",
                                         "summary": "Kern 1", "snippet": "Auszug agent-memory/a/L1.",
                                         "valid_from": "2026-09-01"}

    def test_superseded_hits_are_dropped(self):
        search = FakeSearch({"agent-memory/": [_hit("agent-memory/old", superseded_by="L9"),
                                               _hit("agent-memory/new"),
                                               _hit("native-x/kept", superseded_by="")]})
        learnings = bank.pools_for_case(_pool_case(), search)["learnings"]
        # nur ein gesetztes superseded_by verwirft; cids zaehlen erst nach dem Filtern
        assert [c["rid"] for c in learnings] == ["agent-memory/new", "native-x/kept"]
        assert [c["cid"] for c in learnings] == ["l01", "l02"]

    def test_demoted_prefix_goes_last(self):
        search = FakeSearch({"agent-memory/": [_hit("haupt-wiki/queries/q1"), _hit("agent-memory/a"),
                                               _hit("haupt-wiki/queries/q2"), _hit("native-x/b")]})
        pools = bank.pools_for_case(_pool_case(), search)
        assert [c["rid"] for c in pools["learnings"]] == [
            "agent-memory/a", "native-x/b", "haupt-wiki/queries/q1", "haupt-wiki/queries/q2"]
        assert [c["cid"] for c in pools["learnings"]] == ["l01", "l02", "l03", "l04"]

    def test_repeated_record_keeps_first_chunk_only(self):
        search = FakeSearch({"atlas/skill:": [_hit("atlas/skill:foo", snippet="erster"),
                                              _hit("atlas/skill:foo", snippet="zweiter")]})
        (cand,) = bank.pools_for_case(_pool_case(), search)["skills"]
        assert cand["snippet"] == "erster"

    def test_empty_query_skips_search(self):
        search = FakeSearch()
        pools = bank.pools_for_case(_pool_case(prompt="ok", prev_answer=""), search)
        assert search.bodies == [] and pools == {pot: [] for pot in v2.POTS}

    def test_failed_search_is_loud(self):
        import pytest
        with pytest.raises(RuntimeError, match="C1"):
            bank.pools_for_case(_pool_case(), lambda body: None)


def _pools_with(*rids):
    return {"learnings": [{"cid": f"l{i:02d}", "pot": "learnings", "rid": rid, "summary": f"Kern {i}",
                           "snippet": f"Auszug {i}", "valid_from": "2026-09-01"}
                          for i, rid in enumerate(rids, 1)],
            "skills": [], "capabilities": []}


def _batch_row(cid, pools, session_start=False):
    return {"case_id": cid, "session_start": session_start, "cwd": "C:\\proj", "prompt": f"prompt {cid}",
            "prev_answer": "antwort", "system": v2.GATE_SYSTEM_V2, "user": "...", "pools": pools}


def _reply(*cids):
    return json.dumps({"learnings": [{"cid": c, "why": "passt"} for c in cids], "skills": [],
                       "capabilities": [], "clarify": ""})


class TestPrepare:
    def test_batch_rows_carry_system_user_pools_and_no_gold(self):
        case = _pool_case()
        pools = _pools_with("agent-memory/a")
        row = bank.gate_row(case, pools)
        assert row["case_id"] == "C1" and row["pools"] == pools
        assert row["system"] == v2.GATE_SYSTEM_V2
        assert row["user"] == v2.render_gate_prompt(case["prompt"], case["prev_answer"], case["cwd"], pools)
        for secret in ("positives", "negatives", "expect_nothing", "already_static"):
            assert secret not in row

    def test_prepare_writes_numbered_batches(self, tmp_path):
        cases = [_pool_case(case_id=f"C{i}") for i in range(1, 6)]
        (tmp_path / "cases.jsonl").write_text(
            "".join(json.dumps(c) + "\n" for c in cases), encoding="utf-8")
        search = FakeSearch({"agent-memory/": [_hit("agent-memory/a")]})
        assert bank.main(["prepare", "--cases", str(tmp_path / "cases.jsonl"), "--bank-dir", str(tmp_path),
                          "--batch-size", "2"], search=search) == 0
        files = sorted((tmp_path / "gate_batches").glob("*.jsonl"))
        assert [f.name for f in files] == ["01.jsonl", "02.jsonl", "03.jsonl"]
        rows = [json.loads(line) for f in files for line in f.read_text(encoding="utf-8").splitlines()]
        assert [r["case_id"] for r in rows] == ["C1", "C2", "C3", "C4", "C5"]
        assert rows[0]["pools"]["learnings"][0]["cid"] == "l01"
        assert len(search.bodies) == 5 * len(v2.POTS)


class TestCollect:
    def test_collect_skips_cases_without_gate_reply(self):
        pools = _pools_with("agent-memory/a", "native-x/b")
        rows = [_batch_row("C1", pools), _batch_row("C2", pools)]
        results, judge, missing = bank.collect_batch(rows, {"C1": _reply("l02")})
        assert [r["case_id"] for r in results] == ["C1"] and missing == ["C2"]
        assert results[0]["kept"]["learnings"] == [{"cid": "l02", "why": "passt", "rid": "native-x/b"}]
        assert results[0]["is_parsed"] is True

    def test_unknown_cid_and_garbage_reply_keep_nothing_but_are_recorded(self):
        pools = _pools_with("agent-memory/a")
        rows = [_batch_row("C1", pools), _batch_row("C2", pools)]
        results, judge, missing = bank.collect_batch(rows, {"C1": _reply("l09"), "C2": "kein json"})
        assert missing == []
        assert [r["kept"]["learnings"] for r in results] == [[], []]
        assert [r["is_parsed"] for r in results] == [True, False]
        assert judge == []

    def test_session_start_allows_third_learning(self):
        pools = _pools_with("agent-memory/a", "native-x/b", "native-x/c")
        reply = _reply("l01", "l02", "l03")
        (normal,), _, _ = bank.collect_batch([_batch_row("C1", pools)], {"C1": reply})
        (start,), _, _ = bank.collect_batch([_batch_row("C1", pools, session_start=True)], {"C1": reply})
        assert len(normal["kept"]["learnings"]) == 2 and len(start["kept"]["learnings"]) == 3

    def test_judge_rows_are_blind_and_one_per_kept_entry(self):
        pools = _pools_with("agent-memory/a", "native-x/b")
        row = _batch_row("C1", pools)
        _, judge, _ = bank.collect_batch([row], {"C1": _reply("l01", "l02")})
        assert [(j["case_id"], j["rid"]) for j in judge] == [("C1", "agent-memory/a"), ("C1", "native-x/b")]
        assert judge[0] == {"case_id": "C1", "rid": "agent-memory/a", "prompt": "prompt C1",
                            "context": "antwort", "cwd": "C:\\proj", "summary": "Kern 1",
                            "snippet": "Auszug 1"}
        for key in ("pot", "cid", "valid_from", "source", "variant", "why"):
            assert key not in judge[0]

    def test_collect_cli_reads_numbered_files_and_writes_results(self, tmp_path, capsys):
        pools = _pools_with("agent-memory/a")
        (tmp_path / "gate_batches").mkdir()
        (tmp_path / "gate_out").mkdir()
        (tmp_path / "gate_batches" / "01.jsonl").write_text(
            json.dumps(_batch_row("C1", pools)) + "\n" + json.dumps(_batch_row("C2", pools)) + "\n",
            encoding="utf-8")
        (tmp_path / "gate_batches" / "02.jsonl").write_text(
            json.dumps(_batch_row("C3", pools)) + "\n", encoding="utf-8")
        (tmp_path / "gate_out" / "01.jsonl").write_text(
            json.dumps({"case_id": "C1", "reply": _reply("l01")}) + "\n", encoding="utf-8")
        assert bank.main(["collect", "--bank-dir", str(tmp_path)]) == 0
        res = [json.loads(x) for x in (tmp_path / "gate_results.jsonl").read_text(encoding="utf-8").splitlines()]
        assert [r["case_id"] for r in res] == ["C1"]
        judge = [json.loads(x) for x in (tmp_path / "judge_batches" / "01.jsonl").read_text(
            encoding="utf-8").splitlines()]
        assert [(j["case_id"], j["rid"]) for j in judge] == [("C1", "agent-memory/a")]
        assert not (tmp_path / "judge_batches" / "02.jsonl").exists()
        out = capsys.readouterr().out
        assert "1 von 3" in out and "C2" in out and "C3" in out


class TestBriefs:
    def test_briefs_exist_with_contract_terms(self):
        from pathlib import Path
        here = Path(bank.__file__).resolve().parent
        gate = (here / "GATE_V2_BRIEF.md").read_bytes().decode("utf-8")
        judge = (here / "JUDGE_V2_BRIEF.md").read_bytes().decode("utf-8")
        assert "GATE_SYSTEM_V2" in gate and "gate_out" in gate and '"case_id"' in gate
        for label in ("relevant", "marginal", "noise", "duplicate", "misleading"):
            assert label in judge
        assert "judge_out" in judge and "opus" in judge.lower()
        for text in (gate, judge):
            assert text.count("\r\n") == text.count("\n") > 0


class TestMakeSearch:
    def test_posts_body_to_search_endpoint_caches_and_counts_failures(self, monkeypatch):
        import prompt_prelude as pp
        from replay.round4 import Daemon
        sent = []

        def fake_post(url, body, timeout):
            sent.append((url, body, timeout))
            if body["query"] == "boom":
                raise OSError("down")
            return {"results": [{"record_id": "x"}]}

        monkeypatch.setattr(pp, "_http_post_json", fake_post)
        monkeypatch.setattr(pp, "_daemon_url", lambda: "http://daemon.invalid")
        daemon = Daemon(timeout=7.0)
        search = bank.make_search(daemon)
        body = {"query": "alpha", "k": 3, "caller": "prelude-bank"}
        assert search(body) == [{"record_id": "x"}] and search(dict(body)) == [{"record_id": "x"}]
        assert sent == [("http://daemon.invalid/search", body, 7.0)]
        assert search({"query": "boom"}) is None
        assert (daemon.calls, daemon.failures) == (2, 1)


# --- B5: Auswertung gegen die Ziele -------------------------------------------

L1, L2, N1, N2 = ("agent-memory/a/learnings/L1", "agent-memory/a/learnings/L2",
                  "native-x/n1", "native-x/n2")
S1 = "atlas/skill:foo"


def _scase(cid, positives=(), expect_nothing=False):
    return {"case_id": cid, "positives": list(positives), "negatives": [], "expect_nothing": expect_nothing}


def _toy():
    cases = [_scase("A", [L1, L2]), _scase("B", expect_nothing=True),
             _scase("C", expect_nothing=True), _scase("D", [S1])]
    kept = {"A": {"learnings": [{"cid": "l01", "why": "w", "rid": L1}, {"cid": "l02", "why": "w", "rid": N1}],
                  "skills": [], "capabilities": []},
            "B": {"learnings": [], "skills": [], "capabilities": []},
            "C": {"learnings": [N2], "skills": [], "capabilities": []},
            "D": {"learnings": [], "skills": [], "capabilities": []}}
    labels = {("A", L1): "relevant", ("A", N1): "noise", ("C", N2): "noise"}
    return cases, kept, labels


class TestScore:
    def test_score_shares_on_toy_data(self):
        cases, kept, labels = _toy()
        res = bank.score(cases, kept, labels, {})
        lrn, skl, cap = res["pots"]["learnings"], res["pots"]["skills"], res["pots"]["capabilities"]
        assert lrn["relevant_share"] == 1 / 3 and lrn["noise_share"] == 2 / 3
        assert lrn["missing_better_share"] == 0.0      # Fall A: L1 behalten
        assert lrn["nothing_correct_share"] == 0.5     # B richtig, C nicht
        assert (lrn["n_kept"], lrn["unjudged"], lrn["misleading_confirmed"]) == (3, 0, 0)
        assert skl["relevant_share"] is None and skl["noise_share"] is None   # nichts behalten, nichts zu messen
        assert skl["missing_better_share"] == 1.0      # Fall D hat ein Positiv, nichts behalten
        assert skl["nothing_correct_share"] == 1.0
        assert cap["missing_better_share"] is None     # kein Fall mit Capability-Positiv
        assert res["baseline"] is None

    def test_cases_without_gate_result_are_not_measured(self):
        cases, kept, labels = _toy()
        del kept["D"]
        res = bank.score(cases, kept, labels, {})
        assert res["pots"]["skills"]["missing_better_share"] is None
        assert res["n_cases"] == 3

    def test_unjudged_entries_are_counted_and_stay_out_of_shares(self):
        cases, kept, labels = _toy()
        del labels[("C", N2)]
        lrn = bank.score(cases, kept, labels, {})["pots"]["learnings"]
        assert lrn["unjudged"] == 1 and lrn["n_judged"] == 2
        assert lrn["relevant_share"] == 0.5 and lrn["noise_share"] == 0.5

    def test_marginal_and_duplicate_are_neither_relevant_nor_noise(self):
        cases, kept, labels = _toy()
        labels[("A", N1)] = "marginal"
        labels[("C", N2)] = "duplicate"
        lrn = bank.score(cases, kept, labels, {})["pots"]["learnings"]
        assert lrn["relevant_share"] == 1 / 3 and lrn["noise_share"] == 0.0

    def test_baseline_from_judgments(self):
        judgments = [{"case": "X", "better_missing": "yes", "candidates": [],
                      "items": [{"record_id": "r1", "verdict": "relevant"},
                                {"record_id": "r2", "verdict": "irrelevant"}]},
                     {"case": "Y", "better_missing": "no", "candidates": [],
                      "items": [{"record_id": "r3", "verdict": "irrelevant"},
                                {"record_id": "r4", "verdict": "marginal"}]}]
        base = bank.baseline_from(judgments)
        assert base == {"relevant_share": 0.25, "noise_share": 0.5, "missing_better_share": 0.5}
        cases, kept, labels = _toy()
        assert bank.score(cases, kept, labels, {}, judgments=judgments)["baseline"] == base


GOOD = {"relevant_share": 0.51, "noise_share": 0.14, "missing_better_share": 0.19,
        "nothing_correct_share": 0.80, "misleading_confirmed": 0, "misleading_pending": 0, "unjudged": 0}


class TestPasses:
    def test_passes_thresholds_exactly_at_boundaries(self):
        assert bank.passes(GOOD) is True
        assert bank.passes({**GOOD, "relevant_share": 0.50}) is False      # strikt >
        assert bank.passes({**GOOD, "noise_share": 0.15}) is False         # strikt <
        assert bank.passes({**GOOD, "missing_better_share": 0.20}) is False  # strikt <
        assert bank.passes({**GOOD, "nothing_correct_share": 0.80}) is True  # >=
        assert bank.passes({**GOOD, "nothing_correct_share": 0.79}) is False
        assert bank.passes({**GOOD, "misleading_confirmed": 1}) is False

    def test_unmeasurable_or_unjudged_does_not_pass(self):
        for key in ("relevant_share", "noise_share", "missing_better_share", "nothing_correct_share"):
            assert bank.passes({**GOOD, key: None}) is False
        assert bank.passes({**GOOD, "unjudged": 1}) is False

    def test_unconfirmed_misleading_blocks_until_owner_confirms_or_clears(self):
        # Ein Fall mit einem Positiv (L1) und ein expect_nothing-Fall; Gate behaelt L1 bzw. nichts.
        cases = [_scase("A", [L1]), _scase("B", expect_nothing=True)]
        kept = {"A": {"learnings": [L1], "skills": [], "capabilities": []},
                "B": {"learnings": [], "skills": [], "capabilities": []}}
        relevant = bank.score(cases, kept, {("A", L1): "relevant"}, {})["pots"]["learnings"]
        assert bank.passes(relevant) is True
        labels = {("A", L1): "misleading"}
        # Gutachter markiert, Owner hat noch nicht gesichtet -> blockiert
        pending = bank.score(cases, kept, labels, {})["pots"]["learnings"]
        assert (pending["misleading_confirmed"], pending["misleading_pending"]) == (0, 1)
        assert bank.passes({**relevant, **{k: pending[k] for k in ("misleading_confirmed", "misleading_pending")}}) is False
        # Owner bestaetigt -> blockiert
        confirmed = bank.score(cases, kept, labels, {("A", L1): True})["pots"]["learnings"]
        assert (confirmed["misleading_confirmed"], confirmed["misleading_pending"]) == (1, 0)
        assert bank.passes({**relevant, "misleading_confirmed": 1}) is False
        # Owner entlastet: weder bestaetigt noch offen
        cleared = bank.score(cases, kept, labels, {("A", L1): False})["pots"]["learnings"]
        assert (cleared["misleading_confirmed"], cleared["misleading_pending"]) == (0, 0)

    def test_misleading_gate_decides_a_full_score_result(self):
        cases = [_scase("A", [L1]), _scase("B", expect_nothing=True)]
        kept = {"A": {"learnings": [L1], "skills": [], "capabilities": []},
                "B": {"learnings": [], "skills": [], "capabilities": []}}
        # Ein Treffer relevant, der zweite vom Gutachter als irrefuehrend markiert.
        kept["A"]["learnings"] = [L1, L2]
        labels = {("A", L1): "relevant", ("A", L2): "relevant"}
        assert bank.passes(bank.score(cases, kept, labels, {}), pot="learnings") is True
        labels[("A", L2)] = "misleading"
        res = bank.score(cases, kept, labels, {})
        assert bank.passes(res, pot="learnings") is False                       # offen
        res = bank.score(cases, kept, labels, {("A", L2): True})
        assert bank.passes(res, pot="learnings") is False                       # bestaetigt
        res = bank.score(cases, kept, labels, {("A", L2): False})
        assert res["pots"]["learnings"]["relevant_share"] == 0.5
        assert bank.passes(res, pot="learnings") is False                       # 0,50 ist nicht > 0,50

    def test_score_result_is_checked_per_pot_or_for_all_pots(self):
        cases, kept, labels = _toy()
        res = bank.score(cases, kept, labels, {})
        assert bank.passes(res) is False and bank.passes(res, pot="learnings") is False
        res["pots"]["learnings"] = dict(GOOD)
        assert bank.passes(res, pot="learnings") is True
        assert bank.passes(res) is False    # Skills/Capabilities erreichen die Ziele nicht


class TestScoreCli:
    def test_score_cli_writes_summary_and_prints_table(self, tmp_path, capsys):
        bank_dir = tmp_path / "bank"
        cases, kept, _ = _toy()
        bank._write_jsonl(bank_dir / "cases.jsonl", cases)
        bank._write_jsonl(bank_dir / "gate_results.jsonl",
                          [{"case_id": cid, "kept": k, "clarify": "", "is_parsed": cid != "B"}
                           for cid, k in kept.items()])
        bank._write_jsonl(bank_dir / "judge_out" / "01.jsonl",
                          [{"case_id": "A", "rid": L1, "label": "relevant", "why": "x"},
                           {"case_id": "A", "rid": N1, "label": "misleading", "why": "x"},
                           {"case_id": "C", "rid": N2, "label": "noise", "why": "x"}])
        bank._write_jsonl(bank_dir / "owner_misleading.jsonl", [{"case_id": "A", "rid": N1, "confirmed": False}])
        assert bank.main(["score", "--bank-dir", str(bank_dir), "--judgments", str(tmp_path / "none.json")]) == 0
        summary = json.loads((bank_dir / "summary.json").read_text(encoding="utf-8"))
        lrn = summary["pots"]["learnings"]
        assert lrn["relevant_share"] == 1 / 3 and lrn["misleading_confirmed"] == 0
        assert summary["pass"] == {"learnings": False, "skills": False, "capabilities": False}
        assert summary["unparsed"] == 1 and summary["baseline"] is None
        out = capsys.readouterr().out
        assert "learnings" in out and "skills" in out and "relevant" in out
