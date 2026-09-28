# -*- coding: utf-8 -*-
"""Blinder Relevanz-Judge für den Replay.

Paare (Prompt, Record) werden über alle Varianten gepoolt, dedupliziert und
gemischt — der Judge sieht weder Variante noch Partition. Verdikte (0/1/2)
werden pro Paar gecacht; neue Varianten brauchen nur neue Paare.
Bewertungsschema: replay/JUDGE_BRIEF.md. Plan + vorregistriertes Kriterium:
docs/2026-09-28-plan-relevanz-replay.md.
"""
import hashlib
import json
import random
from pathlib import Path

CONTEXT_CAP = 600
PROMPT_CAP = 1500
USEFUL_MIN_GAIN = 0.10   # vorregistriert: useful_rate >= Baseline + 10 pp
NOISE_MAX_RISE = 0.05    # vorregistriert: noise_rate <= Baseline + 5 pp
_EPS = 1e-9


def pair_id(pid, rid):
    return hashlib.sha1(f"{pid}|{rid}".encode("utf-8")).hexdigest()[:12]


def collect_pairs(runs, corpus):
    """-> {pair_id: judge-Material}; ohne Varianten-/Partitions-Info."""
    pairs = {}
    for rows in runs.values():
        for row in rows:
            entry = corpus.get(row.get("pid"))
            if not entry:
                continue
            for item in row.get("items") or []:
                pid_ = pair_id(entry["pid"], item["rid"])
                if pid_ in pairs:
                    continue
                pairs[pid_] = {"pair_id": pid_,
                               "prompt": str(entry.get("prompt") or "")[:PROMPT_CAP],
                               "context": str(entry.get("prev_assistant") or "")[-CONTEXT_CAP:],
                               "cwd": entry.get("cwd") or "",
                               "hint": item.get("hint") or item["rid"],
                               "snippet": item.get("snippet") or ""}
    return pairs


def make_batches(pairs, cached_ids, batch_size, seed):
    todo = sorted(p for p in pairs if p not in cached_ids)
    random.Random(seed).shuffle(todo)
    rows = [pairs[p] for p in todo]
    return [rows[i:i + batch_size] for i in range(0, len(rows), batch_size)]


def load_verdicts(paths, known):
    """-> ({pair_id: score}, [ungültige Zeilen]). Ungültig: kein JSON,
    unbekannte pair_id, score nicht int 0/1/2 — wird gemeldet, nie still
    verworfen."""
    verdicts, bad = {}, []
    for path in paths:
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                pid_, score = rec["pair_id"], rec["score"]
                ok = pid_ in known and type(score) is int and score in (0, 1, 2)
            except (ValueError, KeyError, TypeError):
                ok = False
            if ok:
                verdicts[pid_] = score
            else:
                bad.append(line[:120])
    return verdicts, bad


def score_runs(runs, verdicts):
    """-> {variant: {gate: {n, material_rate, useful_rate, noise_rate,
    precision_strict, precision_lenient, items, unjudged}}}.
    Alle Raten über ALLE Prompts der Gruppe (gleicher Nenner je Variante)."""
    out = {}
    for name, rows in runs.items():
        groups = {}
        for row in rows:
            g = groups.setdefault(row.get("gate"), {"n": 0, "material": 0, "useful": 0,
                                                    "noise": 0, "items": 0, "strict": 0,
                                                    "lenient": 0, "unjudged": 0})
            g["n"] += 1
            scores = []
            for item in row.get("items") or []:
                s = verdicts.get(pair_id(row["pid"], item["rid"]))
                g["items"] += 1
                if s is None:
                    g["unjudged"] += 1
                    continue
                scores.append(s)
                g["strict"] += s == 2
                g["lenient"] += s >= 1
            if row.get("items"):
                g["material"] += 1
                g["useful"] += any(s == 2 for s in scores)
                g["noise"] += bool(scores) and all(s == 0 for s in scores)
        out[name] = {
            gate: {"n": g["n"],
                   "material_rate": g["material"] / g["n"],
                   "useful_rate": g["useful"] / g["n"],
                   "noise_rate": g["noise"] / g["n"],
                   "precision_strict": g["strict"] / g["items"] if g["items"] else 0.0,
                   "precision_lenient": g["lenient"] / g["items"] if g["items"] else 0.0,
                   "items": g["items"], "unjudged": g["unjudged"]}
            for gate, g in groups.items()}
    return out


def passes_criterion(variant, baseline):
    return (variant["useful_rate"] >= baseline["useful_rate"] + USEFUL_MIN_GAIN - _EPS
            and variant["noise_rate"] <= baseline["noise_rate"] + NOISE_MAX_RISE + _EPS)


def _load_runs(runs_dir):
    return {p.stem: [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l]
            for p in sorted(Path(runs_dir).glob("V*.jsonl"))}


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    data = here / "data"
    ap = argparse.ArgumentParser(description="Replay-Judge: prepare | score")
    ap.add_argument("cmd", choices=["prepare", "score"])
    ap.add_argument("--batch-size", type=int, default=125)
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--runs-dir", default="runs", help="Unterordner von data/ (runs | runs_holdout)")
    args = ap.parse_args(argv)
    corpus = {}
    for line in (data / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            corpus[e["pid"]] = e
    runs = _load_runs(data / args.runs_dir)
    pairs = collect_pairs(runs, corpus)
    # Verdikt-Cache über ALLE Läufe: frühere Paare sind bekannt, nicht ungültig.
    all_pairs = dict(pairs)
    for other in sorted(data.glob("runs*")):
        if other.is_dir():
            all_pairs.update(collect_pairs(_load_runs(other), corpus))
    jdir = data / "judge"
    jdir.mkdir(parents=True, exist_ok=True)
    verdicts, bad = load_verdicts(sorted(jdir.glob("verdicts_*.jsonl")), known=set(all_pairs))
    if args.cmd == "prepare":
        batches = make_batches(pairs, set(verdicts), args.batch_size, args.seed)
        start = len(list(jdir.glob("batch_*.jsonl"))) + 1
        for i, batch in enumerate(batches, start):
            with open(jdir / f"batch_{i:02d}.jsonl", "w", encoding="utf-8") as fh:
                for row in batch:
                    fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"Paare gesamt: {len(pairs)} | schon bewertet: {len(verdicts)} | "
              f"neue Batches: {len(batches)} ({sum(len(b) for b in batches)} Paare)")
        return 0
    metrics = score_runs(runs, verdicts)
    print(f"Verdikte für diesen Lauf: {len(set(verdicts) & set(pairs))}/{len(pairs)} | "
          f"ungültige Zeilen: {len(bad)}")
    for line in bad[:5]:
        print("  UNGÜLTIG:", line)
    for gate in ("pass", "no_work_signal"):
        base = metrics.get("V0", {}).get(gate)
        print(f"\n[{gate}]  n={base['n'] if base else '?'}")
        print("  Var  material  useful  noise  prec2  prec1  items  offen  Kriterium")
        for name in sorted(metrics):
            m = metrics[name].get(gate)
            if not m:
                continue
            crit = "-" if name == "V0" or not base else (
                "ERFÜLLT" if passes_criterion(m, base) else "nein")
            print(f"  {name:<4} {m['material_rate']:>8.0%} {m['useful_rate']:>7.0%} "
                  f"{m['noise_rate']:>6.0%} {m['precision_strict']:>6.0%} "
                  f"{m['precision_lenient']:>6.0%} {m['items']:>6} {m['unjudged']:>6}  {crit}")
    (jdir / "summary.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
