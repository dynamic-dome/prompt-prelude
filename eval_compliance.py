#!/usr/bin/env python3
"""H4: Hook-Compliance messen — Join prompt_prelude.jsonl x tool-usage-tracker.

Beantwortet mit Ground-Truth statt ECHO-Quittung: folgt auf ein fired-Event
des prompt-prelude-Hooks tatsaechlich ein Atlas-Read-Call (memory_search & Co.)
derselben Session innerhalb des Fensters?

Laeuft NUR manuell gegen die echten Logs (nicht CI, nicht Hook-Pfad):

    python eval_compliance.py [--window 900]

Semantik:
- fired-Events: Konsum-Join — jeder Atlas-Call befriedigt hoechstens EIN
  fired-Event (erstes passendes; verhindert Doppelzaehlung bei dichten Prompts).
- skip-Events (deduped/quiet/...): informative Baseline OHNE Konsum — wie oft
  kommt ein Call auch ohne frische Prelude-Zeile?
"""
from __future__ import annotations

import argparse
import json
import sys
from bisect import bisect_left
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TELEMETRY = HERE / "prompt_prelude.jsonl"
DEFAULT_TRACKER_DATA = HERE.parent / "tool-usage-tracker" / "data"
DEFAULT_WINDOW_S = 900.0   # 15 Min: ein Turn kann lange laufen, danach zaehlt es nicht mehr

ATLAS_PREFIX = "mcp__agent-memory-atlas__"
# Nur Read-Tools beweisen "Memory konsultiert"; memory_share ist ein Write.
ATLAS_READ_TOOLS = {
    ATLAS_PREFIX + "memory_search_tool",
    ATLAS_PREFIX + "memory_core_tool",
    ATLAS_PREFIX + "memory_related_tool",
    ATLAS_PREFIX + "memory_cite_tool",
    ATLAS_PREFIX + "memory_status_tool",
}


def iso_to_epoch(ts_utc: str) -> float:
    """Tracker-ts ('2026-07-02T17:25:56.252Z') -> Epoch-Sekunden.
    Ohne Offset/Z ist der Timestamp als UTC zu lesen (nie Lokalzeit)."""
    dt = datetime.fromisoformat(ts_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _iter_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue   # kaputte Zeile: auswerten was geht
    except OSError:
        return


def load_prelude_events(path: str | Path) -> list[dict]:
    """Alle Telemetrie-Zeilen mit Zeit + Session (fired UND skip)."""
    rows = []
    for raw in _iter_jsonl(Path(path)):
        if not isinstance(raw, dict):
            continue
        t, session = raw.get("t"), raw.get("session")
        if isinstance(t, (int, float)) and isinstance(session, str):
            rows.append(raw)
    rows.sort(key=lambda r: r["t"])
    return rows


def load_atlas_calls(data_dir: str | Path) -> list[dict]:
    """Atlas-Read-Calls aus allen (rotierten) events*.jsonl des Trackers.
    Nur PreToolUse (sonst zaehlt ein Call doppelt), dedupe per tool_use_id."""
    calls: list[dict] = []
    seen: set[str] = set()
    for path in sorted(Path(data_dir).glob("events*.jsonl")):
        for raw in _iter_jsonl(path):
            if not isinstance(raw, dict):
                continue
            if raw.get("hook_event") != "PreToolUse":
                continue
            if raw.get("tool_name") not in ATLAS_READ_TOOLS:
                continue
            tuid = raw.get("tool_use_id")
            if isinstance(tuid, str):
                if tuid in seen:
                    continue
                seen.add(tuid)
            ts_utc = raw.get("ts_utc")
            session = raw.get("session_id")
            if not isinstance(ts_utc, str) or not isinstance(session, str):
                continue
            try:
                ts = iso_to_epoch(ts_utc)
            except ValueError:
                continue
            calls.append({"ts": ts, "session": session, "tool": raw["tool_name"]})
    calls.sort(key=lambda c: c["ts"])
    return calls


def _calls_by_session(calls: list[dict]) -> dict[str, list[dict]]:
    by: dict[str, list[dict]] = {}
    for c in calls:
        by.setdefault(c["session"], []).append(c)
    return by


def join_compliance(prelude: list[dict], calls: list[dict],
                    window_s: float = DEFAULT_WINDOW_S) -> list[dict]:
    """Pro Prelude-Zeile: followed/latency_s/matched_tool.
    fired-Events konsumieren ihren Call, skip-Events matchen ohne Konsum."""
    by_session = _calls_by_session(calls)
    consumed: set[int] = set()   # id() der konsumierten Call-Dicts
    rows = []
    for ev in prelude:
        fired = bool(ev.get("fired"))
        session_calls = by_session.get(ev["session"], [])
        ts_list = [c["ts"] for c in session_calls]
        followed, latency, matched = False, None, None
        i = bisect_left(ts_list, ev["t"])
        while i < len(session_calls) and session_calls[i]["ts"] <= ev["t"] + window_s:
            call = session_calls[i]
            if not (fired and id(call) in consumed):
                followed = True
                latency = round(call["ts"] - ev["t"], 1)
                matched = call["tool"]
                if fired:
                    consumed.add(id(call))
                break
            i += 1
        rows.append({
            "t": ev["t"], "session": ev["session"], "fired": fired,
            "skip": ev.get("skip"), "domain": ev.get("domain"),
            "key": ev.get("key"), "routing_source": ev.get("routing_source"),
            "followed": followed, "latency_s": latency, "matched_tool": matched,
        })
    return rows


def _bucket(rows: list[dict], group_key) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in rows:
        g = group_key(r) or "(ohne)"
        slot = out.setdefault(g, {"total": 0, "followed": 0})
        slot["total"] += 1
        slot["followed"] += 1 if r["followed"] else 0
    return out


def summarize(rows: list[dict]) -> dict:
    fired = [r for r in rows if r["fired"]]
    skipped = [r for r in rows if not r["fired"]]

    def _rate(items):
        total = len(items)
        followed = sum(1 for r in items if r["followed"])
        return {"total": total, "followed": followed,
                "rate": round(followed / total, 3) if total else None}

    summary = {"fired": _rate(fired), "skip": _rate(skipped)}
    summary["fired"]["by_domain"] = _bucket(fired, lambda r: r["domain"])
    summary["fired"]["by_routing_source"] = _bucket(fired, lambda r: r["routing_source"])
    summary["skip"]["by_reason"] = _bucket(skipped, lambda r: r["skip"])
    latencies = sorted(r["latency_s"] for r in fired if r["latency_s"] is not None)
    summary["fired"]["median_latency_s"] = (
        latencies[len(latencies) // 2] if latencies else None)
    return summary


def _fmt_bucket(bucket: dict[str, dict]) -> str:
    parts = []
    for name in sorted(bucket, key=lambda n: -bucket[n]["total"]):
        b = bucket[name]
        parts.append(f"    {name:<18} {b['followed']:>3}/{b['total']:<3}"
                     f" ({b['followed'] / b['total']:.0%})")
    return "\n".join(parts) or "    (keine)"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--telemetry", default=str(DEFAULT_TELEMETRY))
    ap.add_argument("--tracker-data", default=str(DEFAULT_TRACKER_DATA))
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_S,
                    help="Match-Fenster in Sekunden (Default 900)")
    ap.add_argument("--json", action="store_true", help="Summary als JSON")
    args = ap.parse_args(argv)

    prelude = load_prelude_events(args.telemetry)
    calls = load_atlas_calls(args.tracker_data)
    rows = join_compliance(prelude, calls, window_s=args.window)
    summary = summarize(rows)

    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0

    f, s = summary["fired"], summary["skip"]
    print(f"Prelude-Zeilen: {len(rows)}  |  Atlas-Read-Calls: {len(calls)}"
          f"  |  Fenster: {args.window:.0f} s")
    print(f"\nFIRED  : {f['followed']}/{f['total']} befolgt"
          f" ({f['rate']:.0%})" if f["total"] else "\nFIRED  : keine Events")
    if f["median_latency_s"] is not None:
        print(f"  Median-Latenz bis zum Call: {f['median_latency_s']:.0f} s")
    print("  nach Domain:")
    print(_fmt_bucket(f["by_domain"]))
    print("  nach routing_source:")
    print(_fmt_bucket(f["by_routing_source"]))
    if s["total"]:
        print(f"\nSKIP-Baseline (ohne Konsum): {s['followed']}/{s['total']}"
              f" ({s['rate']:.0%}) — Calls trotz unterdrueckter Prelude")
        print("  nach Grund:")
        print(_fmt_bucket(s["by_reason"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
