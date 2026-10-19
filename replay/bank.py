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

`prepare` sucht je Fall und Topf die Kandidaten (Zeitreise `as_of` = Prompt-Zeitpunkt) und
schreibt Gate-Pakete `gate_batches/NN.jsonl` ohne Gold-Labels. `collect` liest die Antworten
der Gate-Agenten (`gate_out/NN.jsonl`), parst sie wie der Mod und schreibt `gate_results.jsonl`
plus blinde Gutachter-Pakete `judge_batches/NN.jsonl`. `score` wertet Gate-Ergebnisse, Gutachter-Urteile
(`judge_out/*.jsonl`) und Owner-Bestaetigungen (`owner_misleading.jsonl`) je Topf gegen die Ziele aus
Spec §8 aus und schreibt `summary.json`.
"""
import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import prompt_prelude as pp
from replay import corpus
from replay import v2_contract as v2

MATCH_PREVIEW_CHARS = 25
MATCH_WINDOW_S = 120
_STATIC = "already_in_static_context"
_SUFFIX_RE = re.compile(r"\s+\(.*\)\s*$")
_CID_LETTER = {"learnings": "l", "skills": "s", "capabilities": "c"}
BANK_CALLER = "prelude-bank"
DEFAULT_BATCH_SIZE = 10
# Spec §8 (Erfolg gegenueber heute): relevant > , Rauschen < , fehlend-besser < , nichts-richtig >= .
TARGETS = {"relevant_share": 0.50, "noise_share": 0.15, "missing_better_share": 0.20,
           "nothing_correct_share": 0.80}
# Wortschatz der Gutachter-Labels (JUDGE_V2_BRIEF); alles andere gilt als unbekannt und blockiert.
LABELS = frozenset({"relevant", "marginal", "noise", "duplicate", "misleading"})
DEFAULT_JUDGMENTS = Path.home() / "AI" / "artifacts" / "atlas-injection-recon" / "judge" / "judgments.json"


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


def _candidate(hit, pot):
    return {"pot": pot, "rid": hit["record_id"], "summary": hit.get("summary"),
            "snippet": hit.get("snippet") or "", "valid_from": hit.get("valid_from")}


def pools_for_case(case, search):
    """-> {pot: [Candidate]}. Je Topf eine Suche mit Praefixliste und `as_of` = Prompt-Zeitpunkt
    (Zeitreise: keine Records, die es beim Prompt noch nicht gab). `search(body)` liefert die
    Trefferliste oder None; None bricht laut ab (ein stiller Ausfall sähe wie 'kein Material' aus).
    Überholte Treffer (`superseded_by`) fallen weg, wiederholte Records zählen einmal, `demote`-Praefixe
    rücken ans Ende; die cids (l01, s01, c01 ...) werden erst danach vergeben."""
    query = v2.build_query(case["prompt"], case.get("prev_answer") or "")
    pools = {}
    for pot, spec in v2.POTS.items():
        hits = []
        if query:
            body = {"query": query, "k": spec["k"], "record_id_prefix": list(spec["prefixes"]),
                    "as_of": case["ts"], "caller": BANK_CALLER}
            hits = search(body)
            if hits is None:
                raise RuntimeError(f"Suche fehlgeschlagen: Fall {case['case_id']}, Topf {pot}")
        seen, kept = set(), []
        for hit in hits:
            rid = hit.get("record_id")
            if not rid or hit.get("superseded_by") or rid in seen:
                continue
            seen.add(rid)
            kept.append(hit)
        demote = tuple(spec.get("demote", ()))
        kept.sort(key=lambda h: h["record_id"].startswith(demote))
        pools[pot] = [dict(cid=f"{_CID_LETTER[pot]}{i:02d}", **_candidate(h, pot))
                      for i, h in enumerate(kept, 1)]
    return pools


def make_search(daemon):
    """Daemon-Suche mit dem Body aus `pools_for_case`; nutzt Timeout, Cache und Fehlerzaehler von
    `replay.round4.Daemon`. Jeder Fehler -> None."""
    def search(body):
        key = json.dumps(body, sort_keys=True, ensure_ascii=False)
        if key not in daemon.cache:
            daemon.calls += 1
            try:
                data = pp._http_post_json(pp._daemon_url() + "/search", body, daemon.timeout)
                res = data["results"]
                res = res if isinstance(res, list) else None
            except Exception:
                res = None
            if res is None:
                daemon.failures += 1
            daemon.cache[key] = res
        return daemon.cache[key]
    return search


def gate_row(case, pools):
    """Eine Zeile des Gate-Pakets. Bewusst ohne Gold-Labels (positives, negatives, ...)."""
    prompt, prev = case["prompt"], case.get("prev_answer") or ""
    return {"case_id": case["case_id"], "session_start": bool(case.get("session_start")),
            "cwd": case.get("cwd") or "", "prompt": prompt, "prev_answer": prev,
            "system": v2.GATE_SYSTEM_V2,
            "user": v2.render_gate_prompt(prompt, prev, case.get("cwd") or "", pools),
            "pools": pools}


def collect_batch(rows, replies):
    """Gate-Paket + Antworten -> (results, judge_rows, missing). Faelle ohne Antwort landen nur in
    `missing`. Antworten laufen durch `v2.parse_reply` (wie der Mod); kaputte behalten nichts und
    tragen `is_parsed: False`. Gutachter-Zeilen sind blind: nur Prompt-Material und Treffer, kein
    Topf, keine cid, kein Datum, keine Begruendung des Gates (Anker)."""
    results, judge, missing = [], [], []
    for row in rows:
        reply = replies.get(row["case_id"])
        if reply is None:
            missing.append(row["case_id"])
            continue
        pools = row["pools"]
        parsed = v2.parse_reply(reply, pools, bool(row.get("session_start")))
        by_cid = {c["cid"]: c for cands in pools.values() for c in cands}
        kept = {pot: [dict(entry, rid=by_cid[entry["cid"]]["rid"]) for entry in entries]
                for pot, entries in parsed["kept"].items()}
        results.append({"case_id": row["case_id"], "kept": kept, "clarify": parsed["clarify"],
                        "is_parsed": parsed["is_parsed"]})
        for pot in v2.POTS:
            for entry in parsed["kept"][pot]:
                cand = by_cid[entry["cid"]]
                judge.append({"case_id": row["case_id"], "rid": cand["rid"], "prompt": row["prompt"],
                              "context": str(row.get("prev_answer") or "")[-v2.CONTEXT_CAP:],
                              "cwd": row.get("cwd") or "", "summary": cand.get("summary") or "",
                              "snippet": cand.get("snippet") or ""})
    return results, judge, missing


def _read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")


def _prepare(args, search):
    bank_dir = Path(args.bank_dir)
    cases = _read_jsonl(args.cases)
    if search is None:
        from replay.round4 import Daemon
        search = make_search(Daemon(timeout=args.timeout))
    size = max(1, args.batch_size)
    rows = [gate_row(c, pools_for_case(c, search)) for c in cases]
    out = bank_dir / "gate_batches"
    for n, start in enumerate(range(0, len(rows), size), 1):
        _write_jsonl(out / f"{n:02d}.jsonl", rows[start:start + size])
    print(f"{len(rows)} Faelle -> {out} ({-(-len(rows) // size)} Pakete)")
    return 0


def _collect(args):
    bank_dir = Path(args.bank_dir)
    results, missing, total = [], [], 0
    for batch in sorted((bank_dir / "gate_batches").glob("*.jsonl")):
        rows = _read_jsonl(batch)
        total += len(rows)
        out = bank_dir / "gate_out" / batch.name
        replies = {r["case_id"]: r["reply"] for r in _read_jsonl(out)} if out.exists() else {}
        res, judge, miss = collect_batch(rows, replies)
        results += res
        missing += miss
        if judge:
            _write_jsonl(bank_dir / "judge_batches" / batch.name, judge)
    _write_jsonl(bank_dir / "gate_results.jsonl", results)
    unparsed = [r["case_id"] for r in results if not r["is_parsed"]]
    print(f"Antworten: {len(results)} von {total} Faellen -> {bank_dir / 'gate_results.jsonl'}")
    print(f"ohne Antwort: {len(missing)}" + (f" ({', '.join(missing)})" if missing else ""))
    print(f"nicht lesbar: {len(unparsed)}" + (f" ({', '.join(unparsed)})" if unparsed else ""))
    return 0


def pot_of(rid):
    """Topf einer record_id nach den Praefixen von `v2.POTS`; sonst None."""
    for pot, spec in v2.POTS.items():
        if str(rid).startswith(tuple(spec["prefixes"])):
            return pot
    return None


def _kept_rid(entry):
    return entry if isinstance(entry, str) else entry["rid"]


def _share(num, den):
    return num / den if den else None


def baseline_from(judgments):
    """Kennzahlen des alten Hooks aus `judgments.json` (Plaetze: Verdikt relevant / irrelevant;
    Faelle: `better_missing == yes`). Spec §8 nennt 8 % / 63 % / 56 %."""
    items = [i["verdict"] for j in judgments for i in j.get("items") or []]
    return {"relevant_share": _share(items.count("relevant"), len(items)),
            "noise_share": _share(items.count("irrelevant"), len(items)),
            "missing_better_share": _share(sum(1 for j in judgments if j.get("better_missing") == "yes"),
                                           len(judgments))}


def score(cases, kept, labels, owner, judgments=None):
    """Messbank-Auswertung je Topf.

    cases: Faelle aus `build`; kept: {case_id: {pot: [rid | {"rid": ...}]}} (Zeilen von
    gate_results.jsonl passen direkt); labels: {(case_id, rid): Gutachter-Label}; owner:
    {(case_id, rid): bestaetigt?}. Nur Faelle mit Gate-Ergebnis zaehlen. Anteile ohne Nenner sind
    None (nicht messbar). Behaltene Eintraege ohne Label bleiben aus den Anteilen heraus und stehen
    in `unjudged`; Eintraege mit einem Label ausserhalb von LABELS (z. B. das alte `irrelevant`)
    ebenso, gezaehlt in `unknown_labels`. `misleading_pending` = vom Gutachter markiert, vom Owner noch nicht gesichtet."""
    measured = [c for c in cases if c["case_id"] in kept]
    ids = {c["case_id"] for c in measured}
    pots = {}
    for pot in v2.POTS:
        entries = [(cid, _kept_rid(e)) for cid, by_pot in kept.items() if cid in ids
                   for e in by_pot.get(pot, [])]
        unknown = [k for k in entries if k in labels and labels[k] not in LABELS]
        judged = [k for k in entries if k in labels and labels[k] in LABELS]
        count = Counter(labels[k] for k in judged)
        flagged = [k for k in judged if labels[k] == "misleading"]
        with_positive = [c for c in measured if any(pot_of(p) == pot for p in c.get("positives") or [])]
        missing = [c for c in with_positive
                   if not set(c["positives"]) & {_kept_rid(e) for e in kept[c["case_id"]].get(pot, [])}]
        expect = [c for c in measured if c.get("expect_nothing")]
        quiet = [c for c in expect if not kept[c["case_id"]].get(pot)]
        pots[pot] = {
            "n_kept": len(entries), "n_judged": len(judged), "unjudged": sum(1 for k in entries if k not in labels),
            "unknown_labels": len(unknown),
            "relevant_share": _share(count["relevant"], len(judged)),
            "noise_share": _share(count["noise"], len(judged)),
            "missing_better_share": _share(len(missing), len(with_positive)),
            "nothing_correct_share": _share(len(quiet), len(expect)),
            "misleading_confirmed": sum(1 for k in flagged if owner.get(k) is True),
            "misleading_pending": sum(1 for k in flagged if k not in owner),
        }
    return {"n_cases": len(measured), "pots": pots,
            "baseline": baseline_from(judgments) if judgments is not None else None}


def _meets(m):
    if m["misleading_confirmed"] or m.get("misleading_pending", 0) or m.get("unjudged", 0)             or m.get("unknown_labels", 0):
        return False
    shares = (m["relevant_share"], m["noise_share"], m["missing_better_share"], m["nothing_correct_share"])
    if any(s is None for s in shares):
        return False
    relevant, noise, missing, nothing = shares
    return relevant > TARGETS["relevant_share"] and noise < TARGETS["noise_share"] \
        and missing < TARGETS["missing_better_share"] and nothing >= TARGETS["nothing_correct_share"]


def passes(result, pot=None):
    """Spec §8 exakt: relevant > 0,50, Rauschen < 0,15, fehlend-besser < 0,20, 'nichts' richtig
    >= 0,80, irrefuehrend bestaetigt == 0 (ein noch ungesichtetes Gutachter-'misleading' und
    ungelabelte oder unbekannt gelabelte Eintraege blockieren ebenfalls). `result` ist die Kennzahlen eines Topfs oder das
    Ergebnis von `score`; dann gilt `pot`, ohne Angabe alle Toepfe."""
    if "pots" not in result:
        return _meets(result)
    chosen = [pot] if pot else list(result["pots"])
    return all(_meets(result["pots"][p]) for p in chosen)


def _score(args):
    bank_dir = Path(args.bank_dir)
    cases = _read_jsonl(bank_dir / "cases.jsonl")
    results = _read_jsonl(bank_dir / "gate_results.jsonl")
    kept = {r["case_id"]: r["kept"] for r in results}
    labels = {}
    for batch in sorted((bank_dir / "judge_out").glob("*.jsonl")):
        for r in _read_jsonl(batch):
            labels[(r["case_id"], r["rid"])] = r["label"]
    owner_file = bank_dir / "owner_misleading.jsonl"
    owner = {(r["case_id"], r["rid"]): bool(r["confirmed"]) for r in _read_jsonl(owner_file)} \
        if owner_file.exists() else {}
    judgments_file = Path(args.judgments)
    judgments = _load(judgments_file) if judgments_file.exists() else None
    res = score(cases, kept, labels, owner, judgments=judgments)
    res["unparsed"] = sum(1 for r in results if not r.get("is_parsed"))
    res["targets"] = TARGETS
    res["pass"] = {pot: passes(res, pot=pot) for pot in res["pots"]}
    (bank_dir / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")
    _print_table(res)
    return 0


def _pct(value):
    return "-" if value is None else f"{value * 100:.0f} %"


def _print_table(res):
    pots, base = res["pots"], res["baseline"] or {}
    print(f"{res['n_cases']} Faelle ausgewertet, {res['unparsed']} Antworten nicht lesbar")
    print(f"{'Kennzahl':<22}{'heute':>7}{'Ziel':>9}" + "".join(f"{p:>14}" for p in pots))
    rows = (("relevant_share", "relevant", "> 50 %"), ("noise_share", "Rauschen", "< 15 %"),
            ("missing_better_share", "fehlend besser", "< 20 %"), ("nothing_correct_share", "nichts richtig", ">= 80 %"))
    for key, name, goal in rows:
        print(f"{name:<22}{_pct(base.get(key)):>7}{goal:>9}" + "".join(f"{_pct(m[key]):>14}" for m in pots.values()))
    for key, name in (("misleading_confirmed", "irrefuehrend (Owner)"), ("misleading_pending", "irrefuehrend offen"),
                      ("unjudged", "ohne Urteil"), ("unknown_labels", "unbekanntes Label")):
        print(f"{name:<22}{'':>7}{('0' if key not in ('unjudged', 'unknown_labels') else ''):>9}"
              + "".join(f"{m[key]:>14}" for m in pots.values()))
    print(f"{'bestanden':<22}{'':>7}{'':>9}" + "".join(f"{('ja' if res['pass'][p] else 'nein'):>14}" for p in pots))


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _read_transcript(path):
    try:
        return list(corpus.iter_human_prompts(path))
    except OSError:
        return []


def main(argv=None, search=None):
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Messbank-Faelle aus der Recon bauen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="Faelle bauen")
    b.add_argument("--artifacts", required=True, help="Ordner mit events.json, sample.json, judge/judgments.json")
    b.add_argument("--out", default=str(here / "data" / "bank" / "cases.jsonl"))
    pr = sub.add_parser("prepare", help="Pools suchen und Gate-Pakete schreiben")
    pr.add_argument("--cases", default=str(here / "data" / "bank" / "cases.jsonl"))
    pr.add_argument("--bank-dir", default=str(here / "data" / "bank"))
    pr.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    pr.add_argument("--timeout", type=float, default=15.0)
    co = sub.add_parser("collect", help="Gate-Antworten einlesen, Gutachter-Pakete schreiben")
    co.add_argument("--bank-dir", default=str(here / "data" / "bank"))
    sc = sub.add_parser("score", help="Gate-Ergebnisse und Gutachter-Urteile gegen die Ziele auswerten")
    sc.add_argument("--bank-dir", default=str(here / "data" / "bank"))
    sc.add_argument("--judgments", default=str(DEFAULT_JUDGMENTS), help="judgments.json fuer die Baseline (optional)")
    args = ap.parse_args(argv)
    if args.cmd == "prepare":
        return _prepare(args, search)
    if args.cmd == "collect":
        return _collect(args)
    if args.cmd == "score":
        return _score(args)

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
