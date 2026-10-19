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
