# -*- coding: utf-8 -*-
"""Runde 2: Item-Gate per Kosinus-Ähnlichkeit (Kandidat "P").

Vorregistriertes Verfahren (docs/2026-09-28-plan-relevanz-replay.md):
Split nach sha1(pid)-Parität, τ nur auf Hälfte A wählen, einmal auf B messen.
Kandidaten = V1-Items (normale Suche + Projekt-Partition).
"""
import hashlib
import json
from pathlib import Path

import prompt_prelude as pp
from replay import judge as jd

TAUS = [round(0.20 + 0.05 * i, 2) for i in range(9)]  # 0.20 … 0.60
NOISE_FACTOR = 0.5
USEFUL_TOLERANCE = 0.02
_EPS = 1e-9


def half_of(pid):
    return int(hashlib.sha1(str(pid).encode("utf-8")).hexdigest(), 16) % 2


def apply_gate(rows, scores_by_pid, tau):
    return [dict(r, items=pp.gate_items(r.get("items"), scores_by_pid.get(r["pid"]), tau))
            for r in rows]


def _pass_metrics(rows, verdicts):
    return jd.score_runs({"x": rows}, verdicts)["x"].get("pass")


def _meets(m, base):
    return (m["noise_rate"] <= NOISE_FACTOR * base["noise_rate"] + _EPS
            and m["useful_rate"] >= base["useful_rate"] - USEFUL_TOLERANCE - _EPS)


def select_tau(v0_rows, cand_rows, scores_by_pid, verdicts, taus=TAUS):
    """Kleinstes τ, das auf diesen Rows beide Bedingungen erfüllt; sonst das τ
    mit der kleinsten noise_rate unter der useful-Bedingung (Gleichstand ->
    kleineres τ); erfüllt keines die useful-Bedingung -> kleinstes τ."""
    base = _pass_metrics(v0_rows, verdicts)
    fallback = None
    for tau in taus:
        m = _pass_metrics(apply_gate(cand_rows, scores_by_pid, tau), verdicts)
        if _meets(m, base):
            return tau
        if m["useful_rate"] >= base["useful_rate"] - USEFUL_TOLERANCE - _EPS:
            if fallback is None or m["noise_rate"] < fallback[1] - _EPS:
                fallback = (tau, m["noise_rate"])
    return fallback[0] if fallback else taus[0]


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    data = here / "data"
    ap = argparse.ArgumentParser(description="Runde 2: Item-Gate scoren, τ auf A wählen, auf B messen")
    ap.add_argument("--rescore", action="store_true", help="Kosinus-Scores neu vom Daemon holen")
    args = ap.parse_args(argv)
    corpus = {}
    for line in (data / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            corpus[e["pid"]] = e
    runs = jd._load_runs(data / "runs")
    v0, v1 = runs["V0"], runs["V1"]
    scores_path = data / "gate_scores.json"
    if scores_path.exists() and not args.rescore:
        scores = json.loads(scores_path.read_text(encoding="utf-8"))
    else:
        scores, failed = {}, 0
        for row in v1:
            s = pp.score_items(corpus[row["pid"]]["prompt"], row["items"], timeout=10.0)
            if s is None:
                failed += 1
                s = {}
            scores[row["pid"]] = s
        scores_path.write_text(json.dumps(scores), encoding="utf-8")
        print(f"Scores geholt: {len(scores)} Prompts, {failed} Daemon-Fehler")
    verdicts, bad = jd.load_verdicts(sorted((data / "judge").glob("verdicts_*.jsonl")),
                                     known=set(jd.collect_pairs(runs, corpus)))
    split = {h: ([r for r in v0 if half_of(r["pid"]) == h], [r for r in v1 if half_of(r["pid"]) == h])
             for h in (0, 1)}
    tau = select_tau(split[0][0], split[0][1], scores, verdicts)
    print(f"τ gewählt auf A: {tau}  (ungültige Verdikte: {len(bad)})")
    for h, label in ((0, "A (Auswahl)"), (1, "B (Prüfung)")):
        v0h, v1h = split[h]
        p = apply_gate(v1h, scores, tau)
        m = jd.score_runs({"V0": v0h, "V1": v1h, "P": p}, verdicts)
        for gate in ("pass", "no_work_signal"):
            base = m["V0"].get(gate)
            if not base:
                continue
            print(f"\n[{label} · {gate}] n={base['n']}")
            print("  Var  material  useful  noise  treffsicher  items")
            for name in ("V0", "V1", "P"):
                x = m[name][gate]
                hit = x["useful_rate"] / x["material_rate"] if x["material_rate"] else 0.0
                print(f"  {name:<4} {x['material_rate']:>8.0%} {x['useful_rate']:>7.0%} "
                      f"{x['noise_rate']:>6.0%} {hit:>11.0%} {x['items']:>6}")
            if h == 1:
                ok = _meets(m["P"][gate], base)
                print(f"  Kriterium (noise <= 0,5 x V0, useful >= V0 - 2 pp): "
                      f"{'ERFÜLLT' if ok else 'nicht erfüllt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
