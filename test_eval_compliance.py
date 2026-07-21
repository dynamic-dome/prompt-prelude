# test_eval_compliance.py
"""H4: Compliance-Join prompt_prelude.jsonl x tool-usage-tracker events.jsonl.

Beweist die Join-Logik an synthetischen Daten (tmp_path, nie echte Logs):
folgt auf ein fired-Event tatsaechlich ein Atlas-Read-Call derselben Session
innerhalb des Fensters?"""
import json

import pytest

import eval_compliance as ec


def _write_jsonl(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _prelude(t, session, fired=True, **kw):
    row = {"t": t, "session": session, "key": kw.pop("key", "debug"),
           "domain": kw.pop("domain", "debug") if fired else None}
    if fired:
        row["fired"] = True
    else:
        row["skip"] = kw.pop("skip", "deduped")
    row.update(kw)
    return row


def _call(ts_utc, session, tool="mcp__agent-memory-atlas__memory_search_tool",
          hook_event="PreToolUse", tool_use_id="tu-1"):
    return {"ts_utc": ts_utc, "session_id": session, "tool_name": tool,
            "hook_event": hook_event, "tool_use_id": tool_use_id}


# Epoch von 2026-07-02T12:00:00Z, damit ISO<->epoch-Join getestet wird.
T0 = 1782993600.0
ISO0 = "2026-07-02T12:00:00.000Z"
ISO_PLUS_60 = "2026-07-02T12:01:00.000Z"
ISO_PLUS_2H = "2026-07-02T14:00:00.000Z"


def test_iso_to_epoch_roundtrip():
    assert ec.iso_to_epoch(ISO0) == pytest.approx(T0)


def test_iso_to_epoch_without_z_is_still_utc():
    """Codex-Verifier-Finding: ohne trailing Z darf der naive Timestamp nicht
    in Lokalzeit kippen — er ist als UTC zu lesen."""
    assert ec.iso_to_epoch("2026-07-02T12:00:00.000") == pytest.approx(T0)


def test_fired_event_followed_within_window(tmp_path):
    prelude = _write_jsonl(tmp_path / "p.jsonl", [_prelude(T0, "s1")])
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [_call(ISO_PLUS_60, "s1")])
    rows = ec.join_compliance(ec.load_prelude_events(prelude),
                              ec.load_atlas_calls(data), window_s=900)
    assert rows[0]["followed"] is True
    assert rows[0]["latency_s"] == pytest.approx(60.0)
    summary = ec.summarize(rows)
    assert summary["fired"]["total"] == 1
    assert summary["fired"]["followed"] == 1
    assert summary["fired"]["rate"] == 1.0


def test_call_outside_window_or_wrong_session_does_not_count(tmp_path):
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s1"), _prelude(T0, "s2")])
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [
        _call(ISO_PLUS_2H, "s1"),            # zu spaet
        _call(ISO_PLUS_60, "s-fremd"),       # fremde Session
    ])
    rows = ec.join_compliance(ec.load_prelude_events(prelude),
                              ec.load_atlas_calls(data), window_s=900)
    assert all(r["followed"] is False for r in rows)


def test_one_call_satisfies_only_one_fired_event(tmp_path):
    """Konsum-Semantik: EIN Call darf nicht zwei fired-Events gleichzeitig
    als befolgt zaehlen."""
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s1"), _prelude(T0 + 10, "s1")])
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [_call(ISO_PLUS_60, "s1")])
    rows = ec.join_compliance(ec.load_prelude_events(prelude),
                              ec.load_atlas_calls(data), window_s=900)
    assert sum(1 for r in rows if r["followed"]) == 1


def test_loader_filters_post_events_write_tools_and_foreign_tools(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [
        _call(ISO0, "s1", hook_event="PostToolUse", tool_use_id="a"),   # post: raus
        _call(ISO0, "s1", tool="mcp__agent-memory-atlas__memory_share_tool",
              tool_use_id="b"),                                          # write: raus
        _call(ISO0, "s1", tool="Bash", tool_use_id="c"),                 # fremd: raus
        _call(ISO0, "s1", tool_use_id="d"),                              # bleibt
        _call(ISO0, "s1", tool_use_id="d"),                              # Duplikat: raus
    ])
    calls = ec.load_atlas_calls(data)
    assert len(calls) == 1


def test_loader_reads_rotated_files_and_skips_broken_lines(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [_call(ISO0, "s1", tool_use_id="a")])
    _write_jsonl(data / "events.1.jsonl", [_call(ISO0, "s1", tool_use_id="b")])
    (data / "events.2.jsonl").write_text("kein json\n", encoding="utf-8")
    assert len(ec.load_atlas_calls(data)) == 2


def test_skip_events_form_baseline_without_consumption(tmp_path):
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s1"),
        _prelude(T0 + 5, "s1", fired=False),
    ])
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [_call(ISO_PLUS_60, "s1")])
    rows = ec.join_compliance(ec.load_prelude_events(prelude),
                              ec.load_atlas_calls(data), window_s=900)
    summary = ec.summarize(rows)
    # der EINE Call: konsumiert vom fired-Event, zaehlt aber informativ
    # auch fuer die Skip-Baseline (ohne Konsum)
    assert summary["fired"]["followed"] == 1
    assert summary["skip"]["total"] == 1
    assert summary["skip"]["followed"] == 1


def test_min_version_filters_mojibake_versions(tmp_path):
    """Befund 5: v1-v3 (und v=None) sind cp1252-Mojibake — bei min_version=4
    duerfen sie nicht in die Auswertung; v4+ bleibt."""
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s0"),                # ohne v -> raus bei min_version=4
        _prelude(T0 + 1, "s3", v=3),       # v3 -> raus
        _prelude(T0 + 2, "s4", v=4),       # v4 -> bleibt
        _prelude(T0 + 3, "s7", v=7),       # v7 -> bleibt
    ])
    rows = ec.load_prelude_events(prelude, min_version=4)
    assert {r["session"] for r in rows} == {"s4", "s7"}


def test_min_version_zero_keeps_everything(tmp_path):
    """min_version=0 ist die Escape-Luke: alles inkl. v=None wird geladen."""
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s0"),                # ohne v
        _prelude(T0 + 1, "s3", v=3),
        _prelude(T0 + 2, "s4", v=4),
    ])
    rows = ec.load_prelude_events(prelude, min_version=0)
    assert len(rows) == 3


def test_summary_groups_by_domain(tmp_path):
    prelude = _write_jsonl(tmp_path / "p.jsonl", [
        _prelude(T0, "s1", domain="debug", key="debug"),
        _prelude(T0, "s2", domain="ui-frontend", key="ui-frontend"),
    ])
    data = tmp_path / "data"
    data.mkdir()
    _write_jsonl(data / "events.jsonl", [_call(ISO_PLUS_60, "s1")])
    rows = ec.join_compliance(ec.load_prelude_events(prelude),
                              ec.load_atlas_calls(data), window_s=900)
    summary = ec.summarize(rows)
    assert summary["fired"]["by_domain"]["debug"] == {"total": 1, "followed": 1}
    assert summary["fired"]["by_domain"]["ui-frontend"] == {"total": 1, "followed": 0}
