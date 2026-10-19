# -*- coding: utf-8 -*-
"""Messbank fuer Prelude v2: Gold-Faelle aus der Atlas-Injection-Recon vom 2026-10-19.

`build` verbindet die drei Recon-Artefakte (events.json, sample.json,
judge/judgments.json) mit dem Original-Prompt aus dem Claude-Code-Transkript zu
Faellen {case_id, ts, cwd, session_start, prompt, prev_answer, positives, negatives,
expect_nothing, already_static}. Die Ausgabe enthaelt volle Prompts und gehoert
deshalb nur unter `replay/data/bank/` (gitignored - das Repo ist oeffentlich).

Gold-Regeln:
- positives: Records mit Verdikt `relevant` plus die vom Gutachter genannten
  Kandidaten (Suffix " (...)" abgeschnitten). Bei `already_in_static_context`
  zaehlen Kandidaten nicht - das Wissen steht schon im statischen Kontext.
- negatives: Records mit Verdikt `irrelevant` (nicht, wenn dieselbe rid auch positiv ist).
  `marginal` und `duplicate` sind weder noch.
- expect_nothing: kein `relevant`-Treffer und `better_missing` in
  {`no`, `already_in_static_context`}.
"""
import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from replay import corpus

MATCH_PREVIEW_CHARS = 25
MATCH_WINDOW_S = 120
_STATIC = "already_in_static_context"
_SUFFIX_RE = re.compile(r"\s+\(.*\)\s*$")


def _iso_utc(epoch):
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _epoch(ts):
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _strip_candidate(text):
    return _SUFFIX_RE.sub("", str(text)).strip()


def _find_row(event, rows):
    """Transkriptzeile zum Telemetrie-Ereignis: gleiche Sitzung, gleicher
    Prompt-Anfang, naechste Zeit (hoechstens MATCH_WINDOW_S). Sonst None."""
    head = str(event.get("preview") or "").strip()[:MATCH_PREVIEW_CHARS]
    if not head:
        return None
    best = None
    for row in rows:
        if row.get("session") != event.get("session"):
            continue
        if str(row.get("prompt") or "").strip()[:MATCH_PREVIEW_CHARS] != head:
            continue
        at = _epoch(row.get("ts"))
        if at is None:
            continue
        gap = abs(at - float(event["t"]))
        if gap <= MATCH_WINDOW_S and (best is None or gap < best[0]):
            best = (gap, row)
    return best[1] if best else None


def _unique(seq):
    return list(dict.fromkeys(seq))


def build_cases(events, sample, judgments, read_prompts, skipped=None):
    """-> Liste der Faelle. `read_prompts(transcript_path)` liefert Zeilen wie
    `corpus.iter_human_prompts`. Faelle ohne Original-Prompt oder Urteil werden
    uebersprungen und, falls `skipped` (Liste) uebergeben ist, mit ihrer case_id
    dort vermerkt."""
    by_case = {j["case"]: j for j in judgments}
    transcripts = {}
    cases = []
    for entry in sample["cases"]:
        cid = entry["case_id"]
        judgment = by_case.get(cid)
        event = events[entry["event_index"]]
        path = event.get("transcript")
        row = None
        if judgment is not None and path:
            if path not in transcripts:
                transcripts[path] = list(read_prompts(path))
            row = _find_row(event, transcripts[path])
        if judgment is None or row is None:
            if skipped is not None:
                skipped.append(cid)
            continue
        verdicts = [(i["record_id"], i["verdict"]) for i in judgment.get("items") or []]
        relevant = [rid for rid, v in verdicts if v == "relevant"]
        better = judgment.get("better_missing")
        static = better == _STATIC
        candidates = [] if static else [_strip_candidate(c) for c in judgment.get("candidates") or []]
        positives = _unique(relevant + candidates)
        negatives = _unique(rid for rid, v in verdicts if v == "irrelevant" and rid not in positives)
        cases.append({
            "case_id": cid,
            "ts": _iso_utc(event["t"]),
            "cwd": row.get("cwd") or event.get("cwd") or "",
            "session_start": bool(event.get("session_start")),
            "prompt": row["prompt"],
            "prev_answer": row.get("prev_assistant") or "",
            "positives": positives,
            "negatives": negatives,
            "expect_nothing": not relevant and better in ("no", _STATIC),
            "already_static": static,
        })
    return cases


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_transcript(path):
    try:
        return list(corpus.iter_human_prompts(path))
    except OSError:
        return []


def main(argv=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Messbank-Faelle aus der Recon bauen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="Faelle bauen")
    b.add_argument("--artifacts", required=True, help="Ordner mit events.json, sample.json, judge/judgments.json")
    b.add_argument("--out", default=str(here / "data" / "bank" / "cases.jsonl"))
    args = ap.parse_args(argv)

    art = Path(args.artifacts)
    skipped = []
    cases = build_cases(_load(art / "events.json"), _load(art / "sample.json"),
                        _load(art / "judge" / "judgments.json"), _read_transcript, skipped=skipped)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases), encoding="utf-8")
    print(f"{len(cases)} Faelle -> {out}")
    print(f"uebersprungen: {len(skipped)}" + (f" ({', '.join(skipped)})" if skipped else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
