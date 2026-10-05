# -*- coding: utf-8 -*-
"""Schatten-Protokolle des Mods prelude-gate: lesen und zählen."""
import json

from replay import shadow


def _rec(ts, keep, pool_sources=("flat", "caps"), asked=True, answered=True, parsed=True,
         gate_ms=900, pool_ms=200, daemon_failures=0, is_start=False, session="s1"):
    pool = [{"cid": f"c{i + 1:02d}", "rid": f"r/{i}", "source": src}
            for i, src in enumerate(pool_sources)]
    gate = {"model": "haiku", "isAsked": asked}
    if asked:
        gate.update({"isAnswered": answered, "isParsed": parsed, "ms": gate_ms,
                     "usage": {"input_tokens": 1000, "output_tokens": 20,
                               "cache_read_input_tokens": 500,
                               "cache_creation_input_tokens": 0}})
    return {"v": 1, "ts": ts, "session": session, "pool": pool, "keep": keep, "gate": gate,
            "pool_ms": pool_ms, "daemon_failures": daemon_failures, "is_start": is_start}


def _write(tmp_path, day, name, payload):
    folder = tmp_path / day
    folder.mkdir(exist_ok=True)
    text = payload if isinstance(payload, str) else json.dumps(payload)
    (folder / name).write_text(text, encoding="utf-8")


def test_load_sorts_by_time_and_counts_unreadable(tmp_path):
    _write(tmp_path, "2026-10-06", "b.json", _rec(200, []))
    _write(tmp_path, "2026-10-05", "a.json", _rec(100, ["c01"]))
    _write(tmp_path, "2026-10-05", "kaputt.json", "{nicht json")
    _write(tmp_path, "2026-10-05", "fremd.json", {"etwas": "anderes"})
    records, bad = shadow.load_records(tmp_path)
    assert [r["ts"] for r in records] == [100, 200] and bad == 2


def test_summary_counts_keeps_latency_tokens_and_failures():
    records = [
        _rec(1, ["c02"], is_start=True),
        _rec(2, []),
        _rec(3, ["c01", "c02"], gate_ms=2100, session="s2"),
        _rec(4, [], pool_sources=(), asked=False, daemon_failures=4),
        _rec(5, [], answered=False),
        _rec(6, [], parsed=False),
    ]
    s = shadow.summarize(records)
    assert s["records"] == 6 and s["sessions"] == 2 and s["session_starts"] == 1
    assert s["with_keep"] == 2 and s["silent"] == 4 and s["kept_items"] == 3
    assert s["kept_by_source"] == {"caps": 2, "flat": 1}
    assert s["empty_pool"] == 1 and s["daemon_failures"] == 1
    assert s["gate_asked"] == 5 and s["gate_unanswered"] == 1 and s["gate_unparsed"] == 1
    assert s["gate_ms_median"] == 900 and s["gate_ms_p90"] == 2100
    assert s["tokens_in_total"] == 5 * 1500 and s["tokens_out_total"] == 100
    assert s["tokens_in_median"] == 1500


def test_render_handles_empty_and_filled():
    assert "Keine Schatten-Protokolle" in shadow.render(shadow.summarize([]), 0)
    text = shadow.render(shadow.summarize([_rec(1, ["c01"]), _rec(2, [])]), 1)
    assert "Protokolle: 2 aus 1 Sitzungen" in text and "unlesbare Dateien: 1" in text
    assert "Gate behält etwas: 1 (50%)" in text and "schweigt: 1 (50%)" in text


def test_main_prints_stats(tmp_path, capsys):
    _write(tmp_path, "2026-10-05", "a.json", _rec(100, ["c01"]))
    assert shadow.main(["stats", "--dir", str(tmp_path)]) == 0
    assert "Protokolle: 1" in capsys.readouterr().out
