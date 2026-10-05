# -*- coding: utf-8 -*-
"""Schatten-Protokolle des Mods prelude-gate lesen und zusammenfassen.

Der Mod schreibt je getipptem Prompt eine JSON-Datei nach
`<data>/shadow/<Datum>/` (NOTES Befund 17): Kandidaten, Auswahl des Gates,
Wartezeit und Token-Verbrauch. Er spielt nichts ein. Dieses Skript zählt nur;
die Bewertung der Auswahl (Judge) folgt als eigene, vorab registrierte Runde.

    python -m replay.shadow stats --dir <data>/shadow
"""
import json
from pathlib import Path


def load_records(shadow_dir):
    """-> ([Protokolle, älteste zuerst], Anzahl unlesbarer Dateien)."""
    records, bad = [], 0
    for path in sorted(Path(shadow_dir).glob("*/*.json")):
        try:
            rec = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(rec, dict) or "pool" not in rec or "keep" not in rec:
                raise ValueError("kein Schatten-Protokoll")
        except (OSError, ValueError):
            bad += 1
            continue
        records.append(rec)
    records.sort(key=lambda r: r.get("ts") or 0)
    return records, bad


def _quantile(values, q):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))]


def summarize(records):
    """Zahlen über alle Protokolle: wie oft das Gate etwas behält, wie lange
    Suche und Modell brauchen, was ein Prompt an Tokens kostet, was schiefging."""
    n = len(records)
    gates = [r.get("gate") or {} for r in records]
    asked = [g for g in gates if g.get("isAsked")]
    usage = [g.get("usage") or {} for g in asked]
    tokens_in = [int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                 + int(u.get("cache_creation_input_tokens") or 0) for u in usage]
    tokens_out = [int(u.get("output_tokens") or 0) for u in usage]
    gate_ms = [g["ms"] for g in asked if isinstance(g.get("ms"), (int, float))]
    pool_ms = [r["pool_ms"] for r in records if isinstance(r.get("pool_ms"), (int, float))]
    sources = {}
    for r in records:
        by_cid = {c.get("cid"): c for c in r.get("pool") or []}
        for cid in r.get("keep") or []:
            src = (by_cid.get(cid) or {}).get("source") or "?"
            sources[src] = sources.get(src, 0) + 1
    return {
        "records": n,
        "sessions": len({r.get("session") for r in records}),
        "session_starts": sum(1 for r in records if r.get("is_start")),
        "with_keep": sum(1 for r in records if r.get("keep")),
        "silent": sum(1 for r in records if not r.get("keep")),
        "kept_items": sum(len(r.get("keep") or []) for r in records),
        "kept_by_source": dict(sorted(sources.items())),
        "pool_median": _quantile([len(r.get("pool") or []) for r in records], 0.5),
        "empty_pool": sum(1 for r in records if not r.get("pool")),
        "daemon_failures": sum(1 for r in records if (r.get("daemon_failures") or 0) > 0),
        "gate_asked": len(asked),
        "gate_unanswered": sum(1 for g in asked if not g.get("isAnswered")),
        "gate_unparsed": sum(1 for g in asked if g.get("isAnswered") and not g.get("isParsed")),
        "pool_ms_median": _quantile(pool_ms, 0.5),
        "pool_ms_p90": _quantile(pool_ms, 0.9),
        "gate_ms_median": _quantile(gate_ms, 0.5),
        "gate_ms_p90": _quantile(gate_ms, 0.9),
        "tokens_in_total": sum(tokens_in),
        "tokens_out_total": sum(tokens_out),
        "tokens_in_median": _quantile(tokens_in, 0.5),
    }


def render(summary, bad):
    s = summary
    if not s["records"]:
        return f"Keine Schatten-Protokolle gefunden (unlesbare Dateien: {bad})."
    share = lambda k: f"{s[k]} ({s[k] / s['records']:.0%})"
    lines = [
        f"Protokolle: {s['records']} aus {s['sessions']} Sitzungen | Session-Starts: "
        f"{s['session_starts']} | unlesbare Dateien: {bad}",
        f"Gate behält etwas: {share('with_keep')} | schweigt: {share('silent')} | "
        f"behaltene Treffer: {s['kept_items']} nach Herkunft {s['kept_by_source']}",
        f"Kandidaten je Prompt (Median): {s['pool_median']} | ohne Kandidaten: {s['empty_pool']} | "
        f"Prompts mit Daemon-Fehler: {s['daemon_failures']}",
        f"Modell gefragt: {s['gate_asked']} | ohne Antwort: {s['gate_unanswered']} | "
        f"Antwort unlesbar: {s['gate_unparsed']}",
        f"Wartezeit Suche: Median {s['pool_ms_median']} ms, p90 {s['pool_ms_p90']} ms | "
        f"Modell: Median {s['gate_ms_median']} ms, p90 {s['gate_ms_p90']} ms",
        f"Tokens gesamt: {s['tokens_in_total']} Eingabe, {s['tokens_out_total']} Ausgabe | "
        f"Eingabe je Prompt (Median): {s['tokens_in_median']}",
    ]
    return "\n".join(lines)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Schatten-Protokolle von prelude-gate")
    ap.add_argument("cmd", choices=["stats"])
    ap.add_argument("--dir", required=True, help="Ordner mit den Tagesordnern der Protokolle")
    args = ap.parse_args(argv)
    records, bad = load_records(args.dir)
    print(render(summarize(records), bad))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
