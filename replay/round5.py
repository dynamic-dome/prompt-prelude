# -*- coding: utf-8 -*-
"""Runde 5: Relevanz-Gate mit kleinem Modell über einem breiten Kandidaten-Pool.

Plan und vorregistriertes Kriterium: docs/2026-09-28-plan-relevanz-replay.md.

    python -m replay.round5 pool    --data <ordner> --daemon-url <url>
    python -m replay.round5 apply   --data <ordner>
    python -m replay.round5 prepare --data <ordner>
    python -m replay.round5 score   --data <ordner>

`pool` schreibt die Gate-Batches nach `<data>/r5/gate/batch_NN.jsonl`. Das Gate
(ein Subagent je Batch, Brief: replay/GATE_BRIEF.md) schreibt `keep_NN.jsonl`
daneben. `apply` macht daraus den Lauf `G`, `prepare` die Judge-Batches für noch
nicht bewertete Paare, `score` die Auswertung gegen V0 aus Runde 4.
"""
import json
import os
import random
from pathlib import Path

import prompt_prelude as pp
from replay import judge as jd
from replay import round4 as r4

POOL_CAP = 25        # vorregistriert
CAP_TOP = 5          # vorregistriert
KEEP_MAX = 2         # vorregistriert
GATE_BATCH = 25      # vorregistriert
GATE_SNIPPET_CAP = 200
BASELINE_RUN = "I0__V0"
_EPS = 1e-9


def build_pool(entry, search):
    """Breiter Pool: flache Top 12, Capability-Karten aus dem Atlas-Scope, Treffer
    je Scope-Schreibweise des Arbeitsordners. Ohne Dubletten, höchstens POOL_CAP.
    -> [{cid, rid, hint, snippet, source}]"""
    terms = pp.extract_query(entry.get("prompt", ""))
    if not terms.strip():
        return []
    out, seen = [], set()

    def add(results, source):
        for r in results or []:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("record_id") or "")
            if not rid or rid in seen or len(out) >= POOL_CAP:
                continue
            hint = pp.format_cap_hint(r)
            if not hint:
                continue
            seen.add(rid)
            out.append({"cid": f"c{len(out) + 1:02d}", "rid": rid, "hint": hint,
                        "snippet": str(r.get("snippet") or "")[:r4.SNIPPET_CAP],
                        "source": source})

    add(search(terms, None, r4.FLAT_K), "flat")
    add(pp.select_capability_hits(search(terms, pp.CAP_SCOPE, pp.CAP_POT_K),
                                  limit=CAP_TOP, exclude=seen), "caps")
    for scope in pp.project_scopes(entry.get("cwd")):
        add(search(terms, scope, pp.PROJECT_POT_K), "project")
    return out


def gate_row(entry, pool):
    """Was das Gate sieht: kein rid-Mapping nötig, die cid genügt."""
    return {"pid": entry["pid"],
            "prompt": str(entry.get("prompt") or "")[:jd.PROMPT_CAP],
            "context": str(entry.get("prev_assistant") or "")[-jd.CONTEXT_CAP:],
            "cwd": entry.get("cwd") or "",
            "candidates": [{"cid": c["cid"], "hint": c["hint"],
                            "snippet": c["snippet"][:GATE_SNIPPET_CAP]} for c in pool]}


def load_keeps(lines, pools):
    """-> ({pid: [cid]}, [Problem-Texte]). Geprüft wird laut: unbekannte pid,
    doppelte pid, unbekannte cid, mehr als KEEP_MAX (dann zählen die ersten)."""
    keeps, problems = {}, []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            pid, keep = rec["pid"], rec["keep"]
            assert isinstance(keep, list)
        except (ValueError, KeyError, TypeError, AssertionError):
            problems.append(f"unlesbar: {line[:60]}")
            continue
        if pid not in pools:
            problems.append(f"unbekannte pid: {pid}")
            continue
        if pid in keeps:
            problems.append(f"doppelte pid: {pid}")
            continue
        known = {c["cid"] for c in pools[pid]}
        valid = [c for c in keep if c in known]
        if len(valid) != len(keep):
            problems.append(f"unbekannte cid bei {pid}")
        if len(valid) > KEEP_MAX:
            problems.append(f"mehr als {KEEP_MAX} bei {pid}")
            valid = valid[:KEEP_MAX]
        keeps[pid] = valid
    return keeps, problems


def gate_run(entries, pools, keeps):
    """Lauf G im Format der Runde 4: nur was das Gate behalten hat."""
    rows = []
    for e in entries:
        by_cid = {c["cid"]: c for c in pools.get(e["pid"], [])}
        items = [{"rid": by_cid[cid]["rid"], "hint": by_cid[cid]["hint"],
                  "snippet": by_cid[cid]["snippet"], "partition": by_cid[cid]["source"]}
                 for cid in keeps.get(e["pid"], [])]
        rows.append({"pid": e["pid"], "group": e["group"], "items": items})
    return rows


def quiet_criterion(variant, baseline):
    """Leise-Kriterium (beschreibend): halb so viel Rauschen bei gleichem Nutzen."""
    return (variant["noise_rate"] <= 0.5 * baseline["noise_rate"] + _EPS
            and variant["useful_rate"] >= baseline["useful_rate"] - _EPS)


# --- CLI ----------------------------------------------------------------------

def _entries(data):
    """Dieselbe Testmenge wie Runde 4: die pids des Baseline-Laufs."""
    corpus = r4._load_corpus(data)
    base = [json.loads(l) for l in (Path(data) / "r4" / "runs" / f"{BASELINE_RUN}.jsonl")
            .read_text(encoding="utf-8").splitlines() if l]
    return [dict(corpus[row["pid"]], group=row["group"]) for row in base], corpus, base


def _all_verdicts(data, known):
    """Urteile aus Runde 4 und 5. `known` wird um die Paare der Runde-4-Läufe
    erweitert: Urteile zu Paaren, die im aktuellen Vergleich nicht vorkommen,
    sind gültig und zählen nicht als fehlerhafte Zeile."""
    corpus = r4._load_corpus(data)
    known = set(known) | set(r4.collect_pairs(r4._load_runs(Path(data) / "r4"), corpus))
    verdicts, bad = {}, []
    for sub in ("r4", "r5"):
        v, b = r4._load_verdicts(Path(data) / sub, known)
        verdicts.update(v)
        bad.extend(b)
    return verdicts, bad


CONFIRM_BATCH = 80


def confirm_pairs(data, r5):
    """Alle Paare von V0 und G zusammen — für den Bestätigungslauf mit einem
    einheitlichen Judge."""
    _entries_, corpus, base_rows = _entries(data)
    runs = {BASELINE_RUN: base_rows, **r4._load_runs(r5)}
    return runs, r4.collect_pairs(runs, corpus)


def load_confirm_verdicts(lines, known):
    """-> ({pair_id: note}, [fehlerhafte Zeilen])."""
    verdicts, bad = {}, []
    for line in lines:
        if not line.strip():
            continue
        try:
            rec = json.loads(line)
            key, score = rec["pair_id"], rec["score"]
            ok = key in known and type(score) is int and score in (0, 1, 2)
        except (ValueError, KeyError, TypeError):
            ok = False
        if ok:
            verdicts[key] = score
        else:
            bad.append(line[:80])
    return verdicts, bad


def cmd_confirm_prepare(args, r5):
    _runs, pairs = confirm_pairs(args.data, r5)
    todo = sorted(pairs)
    random.Random(args.seed + 100).shuffle(todo)
    cdir = r5 / "confirm"
    cdir.mkdir(parents=True, exist_ok=True)
    batches = [todo[i:i + CONFIRM_BATCH] for i in range(0, len(todo), CONFIRM_BATCH)]
    for i, batch in enumerate(batches, 1):
        with open(cdir / f"batch_{i:02d}.jsonl", "w", encoding="utf-8") as fh:
            for key in batch:
                fh.write(json.dumps(pairs[key], ensure_ascii=False) + "\n")
    print(f"Bestätigungslauf: {len(todo)} Paare (V0 und G gemischt) in {len(batches)} Batches")
    return 0


def cmd_confirm_score(args, r5):
    runs, pairs = confirm_pairs(args.data, r5)
    lines = []
    for path in sorted((r5 / "confirm").glob("verdicts_*.jsonl")):
        lines += path.read_text(encoding="utf-8").splitlines()
    verdicts, bad = load_confirm_verdicts(lines, set(pairs))
    print(f"Urteile (nur Bestätigungslauf): {len(verdicts)}/{len(pairs)} | "
          f"ungültige Zeilen: {len(bad)}")
    first, _ = _all_verdicts(args.data, set(pairs))
    both = [(first[k], verdicts[k]) for k in verdicts if k in first]
    same = sum(1 for a, b in both if a == b)
    print(f"Übereinstimmung mit den ersten Urteilen: {same}/{len(both)} | Note 2 zuerst "
          f"{sum(1 for a, _b in both if a == 2)}, jetzt {sum(1 for _a, b in both if b == 2)}")
    summary = {}
    for group, label in (("start", "Session-Starts"), ("cont", "Fortsetzungen"), (None, "ganz T")):
        print(f"\n[{label}]")
        print("  Lauf        n  material  useful  noise  prec2  prec1  items  offen")
        for name, rows in runs.items():
            m = r4.score_rows(rows, verdicts, group)
            summary.setdefault(name, {})[group or "all"] = m
            print(f"  {name:<10} {m['n']:>3} {m['material_rate']:>8.0%} {m['useful_rate']:>7.0%} "
                  f"{m['noise_rate']:>6.0%} {m['precision_strict']:>6.0%} "
                  f"{m['precision_lenient']:>6.0%} {m['items']:>6} {m['unjudged']:>6}")
    g, b = summary["I0__G"]["all"], summary[BASELINE_RUN]["all"]
    n = g["n"]
    ok = r4.passes(g, b)
    summary["_criteria"] = {"Runde 5 Bestätigung": ok, "Leise-Kriterium": quiet_criterion(g, b)}
    print("\n[Kriterium mit einheitlichem Judge: useful >= +10 pp, noise <= +5 pp, ganz T]")
    print(f"  G gegen V0: useful {round(b['useful_rate'] * n)} -> {round(g['useful_rate'] * n)} "
          f"von {n} ({b['useful_rate']:.0%} -> {g['useful_rate']:.0%}), "
          f"noise {round(b['noise_rate'] * n)} -> {round(g['noise_rate'] * n)} "
          f"({b['noise_rate']:.0%} -> {g['noise_rate']:.0%}) => "
          f"{'ERFÜLLT' if ok else 'nicht erfüllt'}")
    (r5 / "confirm_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


def _load_pools(r5):
    return {json.loads(l)["pid"]: json.loads(l)["pool"]
            for l in (r5 / "pool.jsonl").read_text(encoding="utf-8").splitlines() if l}


def cmd_pool(args, r5):
    entries, _corpus, _base = _entries(args.data)
    daemon = r4.Daemon()
    (r5 / "gate").mkdir(parents=True, exist_ok=True)
    rows = []
    with open(r5 / "pool.jsonl", "w", encoding="utf-8") as fh:
        for e in entries:
            pool = build_pool(e, daemon.search)
            fh.write(json.dumps({"pid": e["pid"], "group": e["group"], "pool": pool},
                                ensure_ascii=False) + "\n")
            rows.append(gate_row(e, pool))
    random.Random(args.seed).shuffle(rows)
    batches = [rows[i:i + GATE_BATCH] for i in range(0, len(rows), GATE_BATCH)]
    for i, batch in enumerate(batches, 1):
        with open(r5 / "gate" / f"batch_{i:02d}.jsonl", "w", encoding="utf-8") as fh:
            for row in batch:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    sizes = sorted(len(r["candidates"]) for r in rows)
    print(f"Prompts: {len(rows)} | Kandidaten gesamt: {sum(sizes)} | je Prompt median "
          f"{sizes[len(sizes) // 2]}, max {sizes[-1]}, ohne Kandidaten {sizes.count(0)} | "
          f"Gate-Batches: {len(batches)} | Daemon-Aufrufe {daemon.calls}, Fehler {daemon.failures}")
    return 0 if not daemon.failures else 1


def cmd_apply(args, r5):
    entries, _corpus, _base = _entries(args.data)
    pools = _load_pools(r5)
    lines = []
    for path in sorted((r5 / "gate").glob("keep_*.jsonl")):
        lines += path.read_text(encoding="utf-8").splitlines()
    keeps, problems = load_keeps(lines, pools)
    missing = [e["pid"] for e in entries if e["pid"] not in keeps]
    rows = gate_run(entries, pools, keeps)
    (r5 / "runs").mkdir(parents=True, exist_ok=True)
    with open(r5 / "runs" / "I0__G.jsonl", "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    kept = sum(len(r["items"]) for r in rows)
    print(f"Gate-Antworten: {len(keeps)}/{len(entries)} | fehlend: {len(missing)} | "
          f"Probleme: {len(problems)} | Prompts mit Material: "
          f"{sum(1 for r in rows if r['items'])} | behaltene Items: {kept}")
    for p in problems[:8]:
        print("  PROBLEM:", p)
    return 0 if not missing and not problems else 1


def cmd_prepare(args, r5):
    _entries_, corpus, _base = _entries(args.data)
    runs = r4._load_runs(r5)
    pairs = r4.collect_pairs(runs, corpus)
    verdicts, _bad = _all_verdicts(args.data, set(pairs))
    todo = sorted(p for p in pairs if p not in verdicts)
    random.Random(args.seed).shuffle(todo)
    jdir = r5 / "judge"
    jdir.mkdir(parents=True, exist_ok=True)
    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    start = len(list(jdir.glob("batch_*.jsonl"))) + 1
    for i, batch in enumerate(batches, start):
        with open(jdir / f"batch_{i:02d}.jsonl", "w", encoding="utf-8") as fh:
            for key in batch:
                fh.write(json.dumps(pairs[key], ensure_ascii=False) + "\n")
    print(f"Paare im Lauf G: {len(pairs)} | schon bewertet (Runde 4 oder 5): "
          f"{len(pairs) - len(todo)} | neu zu bewerten: {len(todo)} in {len(batches)} Batch(es)")
    return 0


def cmd_score(args, r5):
    _entries_, corpus, base_rows = _entries(args.data)
    runs = {BASELINE_RUN: base_rows, **r4._load_runs(r5)}
    pairs = r4.collect_pairs(runs, corpus)
    verdicts, bad = _all_verdicts(args.data, set(pairs))
    print(f"Urteile: {len(set(verdicts) & set(pairs))}/{len(pairs)} | ungültige Zeilen: {len(bad)}")
    summary = {}
    for group, label in (("start", "Session-Starts"), ("cont", "Fortsetzungen"), (None, "ganz T")):
        print(f"\n[{label}]")
        print("  Lauf        n  material  useful  noise  prec2  prec1  items  offen")
        for name, rows in runs.items():
            m = r4.score_rows(rows, verdicts, group)
            summary.setdefault(name, {})[group or "all"] = m
            print(f"  {name:<10} {m['n']:>3} {m['material_rate']:>8.0%} {m['useful_rate']:>7.0%} "
                  f"{m['noise_rate']:>6.0%} {m['precision_strict']:>6.0%} "
                  f"{m['precision_lenient']:>6.0%} {m['items']:>6} {m['unjudged']:>6}")
    g, b = summary["I0__G"]["all"], summary[BASELINE_RUN]["all"]
    n = g["n"]
    ok, quiet = r4.passes(g, b), quiet_criterion(g, b)
    summary["_criteria"] = {"Runde 5": ok, "Leise-Kriterium": quiet}
    print(f"\n[Kriterium, vorregistriert: useful >= +10 pp, noise <= +5 pp, ganz T]")
    print(f"  G gegen V0: useful {round(b['useful_rate'] * n)} -> {round(g['useful_rate'] * n)} "
          f"von {n} ({b['useful_rate']:.0%} -> {g['useful_rate']:.0%}), "
          f"noise {round(b['noise_rate'] * n)} -> {round(g['noise_rate'] * n)} "
          f"({b['noise_rate']:.0%} -> {g['noise_rate']:.0%}) => "
          f"{'ERFÜLLT' if ok else 'nicht erfüllt'}")
    print(f"  Leise-Kriterium (beschreibend): {'erfüllt' if quiet else 'nicht erfüllt'}")
    parts = r4.partition_precision(runs["I0__G"], verdicts)
    print("  Behaltene Items nach Herkunft (n / Note 2 / Note >= 1): "
          + ", ".join(f"{p} {v['n']}/{v['strict']}/{v['lenient']}" for p, v in sorted(parts.items())))
    summary["I0__G"]["partitions"] = parts
    (r5 / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Runde 5: Relevanz-Gate")
    ap.add_argument("cmd", choices=["pool", "apply", "prepare", "score",
                                    "confirm-prepare", "confirm-score"])
    ap.add_argument("--data", required=True, help="Datenordner mit corpus.jsonl und r4/")
    ap.add_argument("--daemon-url", help="Daemon des Index-Builds I0")
    ap.add_argument("--batch-size", type=int, default=125)
    ap.add_argument("--seed", type=int, default=5)
    args = ap.parse_args(argv)
    if args.daemon_url:
        os.environ["ATLAS_DAEMON_URL"] = args.daemon_url
    r5 = Path(args.data) / "r5"
    r5.mkdir(parents=True, exist_ok=True)
    return {"pool": cmd_pool, "apply": cmd_apply,
            "prepare": cmd_prepare, "score": cmd_score,
            "confirm-prepare": cmd_confirm_prepare,
            "confirm-score": cmd_confirm_score}[args.cmd](args, r5)


if __name__ == "__main__":
    raise SystemExit(main())
