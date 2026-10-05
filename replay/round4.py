# -*- coding: utf-8 -*-
"""Runde 4: getrennte Töpfe (B) und Index-Varianten (A).

Plan und vorregistrierte Kriterien: docs/2026-09-28-plan-relevanz-replay.md.

    python -m replay.round4 fit-tau --data <ordner> --index I0 --daemon-url <url>
    python -m replay.round4 run     --data <ordner> --index I0 --daemon-url <url> \
                                    --variants V0,V11,B,Bplus
    python -m replay.round4 prepare --data <ordner>
    python -m replay.round4 score   --data <ordner>

`--data` ist der Datenordner mit `corpus.jsonl` und den Läufen früherer Runden
(lokal, gitignored: volle Prompts). Alles Neue landet unter `<data>/r4/`.
"""
import hashlib
import json
import os
import random
from pathlib import Path

import prompt_prelude as pp
from replay import judge as jd
from replay import run as rr

FLAT_K = 12
CAPS_LIMIT = 3
SNIPPET_CAP = 300
USEFUL_MIN_GAIN = 0.10   # vorregistriert
NOISE_MAX_RISE = 0.05    # vorregistriert
TAU_MIN_STRICT = 0.35    # vorregistriert: Anteil Note 2 unter den behaltenen Paaren
TAU_MIN_KEPT = 0.15      # vorregistriert: Mindestanteil behaltener Paare
_EPS = 1e-9

# Hook-Varianten: (Töpfe statt flacher Suche, Caps/Projekt auch bei Fortsetzungen)
VARIANTS = {
    "V0": {"pots": False, "everywhere": False},     # v12, wie live
    "V11": {"pots": False, "everywhere": True},     # Caps auch bei Fortsetzungen
    "B": {"pots": True, "everywhere": False},       # Töpfe, Politik wie v12
    "Bplus": {"pots": True, "everywhere": True},    # Töpfe auch bei Fortsetzungen
}


# --- Testmenge ------------------------------------------------------------

def is_start(entry):
    return not entry.get("prev_assistant")


def hook_searches(entry):
    """Sucht der v12-Hook bei diesem Prompt? Gate pass, oder Session-Start mit
    Substanz (Bypass von Work-Signal- und Confidence-Gate)."""
    prompt = entry.get("prompt", "")
    gate = rr.gate_of(prompt)
    if gate == "pass":
        return True
    return (is_start(entry) and gate == "no_work_signal"
            and len(str(prompt).strip()) >= pp.SESSION_START_MIN_LEN)


def used_pids(data):
    used = set()
    for sub in ("runs", "runs_holdout"):
        for path in sorted((Path(data) / sub).glob("V*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    used.add(json.loads(line)["pid"])
    return used


def test_set(entries, used):
    """Unberührte Prompts, bei denen der Hook sucht; `group` = start | cont."""
    return [dict(e, group="start" if is_start(e) else "cont")
            for e in entries if e.get("pid") not in used and hook_searches(e)]


# --- Varianten-Logik ------------------------------------------------------

def _item(result, partition):
    return {"rid": str(result.get("record_id")),
            "hint": pp.format_cap_hint(result),
            "snippet": str(result.get("snippet") or "")[:SNIPPET_CAP],
            "partition": partition}


def _mentor_items(flat, terms):
    by_rid = {str(r.get("record_id", "")): r for r in flat if isinstance(r, dict)}
    items = []
    for hint in pp.filter_mentor_results(flat, terms):
        rid = str(hint).split(" — ", 1)[0]
        items.append({"rid": rid, "hint": hint,
                      "snippet": str((by_rid.get(rid) or {}).get("snippet") or "")[:SNIPPET_CAP],
                      "partition": "mentor"})
    return items


def items_flat(entry, search, everywhere):
    """v12-Logik: Caps per atlas/-Präfix aus den Top 12, Mentoren, am
    Session-Start die Projektkarte aus einer zweiten flachen Suche."""
    terms = pp.extract_query(entry.get("prompt", ""))
    flat = search(terms, None, FLAT_K) or []
    start = entry.get("group") == "start"
    items = []
    if start or everywhere:
        caps = [r for r in flat
                if isinstance(r, dict) and str(r.get("record_id", "")).startswith("atlas/")]
        items += [i for i in (_item(r, "caps") for r in caps[:CAPS_LIMIT]) if i["hint"]]
    items += _mentor_items(flat, terms)
    if start:
        slugs = pp.project_slugs(entry.get("cwd"))
        query = pp.project_query(terms, slugs)
        if query:
            seen = {i["rid"] for i in items}
            hits = pp.select_project_hits(search(query, None, FLAT_K), slugs, exclude=seen)
            items += [_item(r, "project") for r in hits]
    return {"terms": terms, "items": items}


def items_pots(entry, search, classify, tau, everywhere):
    """Kandidat B: Mentoren wie bisher, Capabilities und Projekt je aus einer
    eigenen, per scope eingeschränkten Suche; Caps mit Kosinus-Untergrenze."""
    prompt = entry.get("prompt", "")
    terms = pp.extract_query(prompt)
    flat = search(terms, None, FLAT_K) or []
    mentors = _mentor_items(flat, terms)
    items, caps_scored = list(mentors), []
    if (entry.get("group") == "start" or everywhere) and terms.strip():
        seen = {i["rid"] for i in mentors}
        by_scope = [search(terms, scope, pp.PROJECT_POT_K)
                    for scope in pp.project_scopes(entry.get("cwd"))]
        project = [_item(r, "project") for r in pp.select_project_pot(by_scope, exclude=seen)]
        seen |= {i["rid"] for i in project}
        hits = pp.select_capability_hits(search(terms, pp.CAP_SCOPE, pp.CAP_POT_K),
                                         exclude=seen)
        candidates = [i for i in (_item(r, "caps") for r in hits) if i["hint"]]
        scores = classify(prompt, candidates) if candidates else {}
        caps_scored = [{"rid": c["rid"], "cos": (scores or {}).get(c["rid"])}
                       for c in candidates]
        caps = pp.gate_items(candidates, scores, tau) if scores else []
        items = caps + mentors + project
    return {"terms": terms, "items": items, "caps_scored": caps_scored}


def run_variant(name, entries, search, classify, tau):
    cfg = VARIANTS[name]
    rows = []
    for e in entries:
        if cfg["pots"]:
            out = items_pots(e, search, classify, tau, cfg["everywhere"])
        else:
            out = items_flat(e, search, cfg["everywhere"])
        rows.append({"pid": e["pid"], "group": e["group"], **out})
    return rows


# --- Daemon-Zugriff (gecacht, großzügiges Timeout) ---------------------------

class Daemon:
    """Zählt Fehler laut mit: ein stiller Suchausfall sähe sonst aus wie
    'kein Material' und würde die Messung verfälschen."""

    def __init__(self, timeout=15.0):
        self.timeout = timeout
        self.cache = {}
        self.calls = 0
        self.failures = 0

    def search(self, terms, scope, k):
        key = (terms, scope, k)
        if key not in self.cache:
            if not str(terms).strip():
                self.cache[key] = []
            else:
                self.calls += 1
                if scope is None:
                    res = pp.search_via_daemon(terms, k, timeout=self.timeout)
                else:
                    res = pp.search_scoped_via_daemon(terms, scope, k, timeout=self.timeout)
                if res is None:
                    self.failures += 1
                self.cache[key] = res
        return self.cache[key]

    def classify(self, prompt, items):
        self.calls += 1
        scores = pp.score_items(prompt, items, timeout=self.timeout)
        if scores is None:
            self.failures += 1
        return scores


# --- Paare und Urteile ------------------------------------------------------

def pair_key(pid, rid, snippet):
    """Schlüssel aus Prompt, Record UND Snippet: ein Urteil gilt nur weiter,
    wenn alle drei gleich sind (andere Chunks zeigen dem Judge anderen Text)."""
    norm = " ".join(str(snippet or "").split())
    digest = hashlib.sha1(norm.encode("utf-8")).hexdigest()[:8]
    return hashlib.sha1(f"{pid}|{rid}|{digest}".encode("utf-8")).hexdigest()[:12]


def collect_pairs(runs, corpus):
    """-> {pair_id: Judge-Material}; ohne Varianten-, Index- und Partitions-Info."""
    pairs = {}
    for rows in runs.values():
        for row in rows:
            entry = corpus.get(row.get("pid"))
            if not entry:
                continue
            for item in row.get("items") or []:
                key = pair_key(entry["pid"], item["rid"], item.get("snippet"))
                pairs.setdefault(key, {
                    "pair_id": key,
                    "prompt": str(entry.get("prompt") or "")[:jd.PROMPT_CAP],
                    "context": str(entry.get("prev_assistant") or "")[-jd.CONTEXT_CAP:],
                    "cwd": entry.get("cwd") or "",
                    "hint": item.get("hint") or item["rid"],
                    "snippet": item.get("snippet") or ""})
    return pairs


def score_rows(rows, verdicts, group=None):
    """Raten über ALLE Prompts der Gruppe (gleicher Nenner je Variante)."""
    g = {"n": 0, "material": 0, "useful": 0, "noise": 0, "items": 0, "strict": 0,
         "lenient": 0, "unjudged": 0}
    for row in rows:
        if group is not None and row.get("group") != group:
            continue
        g["n"] += 1
        scores = []
        for item in row.get("items") or []:
            s = verdicts.get(pair_key(row["pid"], item["rid"], item.get("snippet")))
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
    n = g["n"] or 1
    return {"n": g["n"], "material_rate": g["material"] / n, "useful_rate": g["useful"] / n,
            "noise_rate": g["noise"] / n,
            "precision_strict": g["strict"] / g["items"] if g["items"] else 0.0,
            "precision_lenient": g["lenient"] / g["items"] if g["items"] else 0.0,
            "items": g["items"], "unjudged": g["unjudged"]}


def passes(variant, baseline):
    return (variant["useful_rate"] >= baseline["useful_rate"] + USEFUL_MIN_GAIN - _EPS
            and variant["noise_rate"] <= baseline["noise_rate"] + NOISE_MAX_RISE + _EPS)


def partition_precision(rows, verdicts):
    out = {}
    for row in rows:
        for item in row.get("items") or []:
            s = verdicts.get(pair_key(row["pid"], item["rid"], item.get("snippet")))
            if s is None:
                continue
            p = out.setdefault(item["partition"], {"n": 0, "strict": 0, "lenient": 0})
            p["n"] += 1
            p["strict"] += s == 2
            p["lenient"] += s >= 1
    return out


# --- Schwelle τ (vorregistrierte Regel) -------------------------------------

def choose_tau(scored):
    """scored: [(kosinus, note)] der alten Caps-Paare. Raster = Dezile.
    Kleinstes Dezil mit Anteil Note 2 >= TAU_MIN_STRICT unter den behaltenen
    und behaltenem Anteil >= TAU_MIN_KEPT; sonst das Dezil mit dem höchsten
    Anteil Note 2 unter denen, die genug behalten.
    -> (tau, [Zeile je Dezil])."""
    vals = sorted(c for c, _s in scored)
    if not vals:
        raise ValueError("keine Paare mit Kosinus-Wert")
    grid = []
    for q in range(1, 10):
        tau = vals[min(len(vals) - 1, int(q / 10 * len(vals)))]
        kept = [s for c, s in scored if c >= tau]
        share = len(kept) / len(scored)
        strict = (sum(1 for s in kept if s == 2) / len(kept)) if kept else 0.0
        grid.append({"decile": q * 10, "tau": round(tau, 4), "kept": len(kept),
                     "kept_share": round(share, 3), "strict": round(strict, 3)})
    eligible = [g for g in grid if g["kept_share"] >= TAU_MIN_KEPT - _EPS]
    for g in eligible:
        if g["strict"] >= TAU_MIN_STRICT - _EPS:
            return g["tau"], grid
    best = max(eligible or grid, key=lambda g: (g["strict"], -g["tau"]))
    return best["tau"], grid


def old_caps_pairs(data):
    """Bewertete Caps-Paare der Runden 1 bis 3: [(pid, rid, snippet, note)]."""
    data = Path(data)
    verdicts = {}
    for path in sorted((data / "judge").glob("verdicts_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                verdicts[rec["pair_id"]] = rec["score"]
    seen, out = set(), []
    for sub in ("runs", "runs_holdout"):
        for path in sorted((data / sub).glob("V*.jsonl")):
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                for item in row.get("items") or []:
                    key = jd.pair_id(row["pid"], item["rid"])
                    if item.get("partition") != "caps" or key in seen or key not in verdicts:
                        continue
                    seen.add(key)
                    out.append((row["pid"], item["rid"], item.get("snippet") or "",
                                verdicts[key]))
    return out


# --- CLI ----------------------------------------------------------------------

def _load_corpus(data):
    corpus = {}
    for line in (Path(data) / "corpus.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            e = json.loads(line)
            corpus[e["pid"]] = e
    return corpus


def _load_runs(r4):
    return {p.stem: [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l]
            for p in sorted((r4 / "runs").glob("*.jsonl"))}


def _load_verdicts(r4, known):
    verdicts, bad = {}, []
    for path in sorted((r4 / "judge").glob("verdicts_*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
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


def cmd_fit_tau(args, r4):
    corpus = _load_corpus(args.data)
    daemon = Daemon()
    by_pid = {}
    for pid, rid, snippet, score in old_caps_pairs(args.data):
        by_pid.setdefault(pid, []).append((rid, snippet, score))
    scored, missing = [], 0
    for pid, rows in by_pid.items():
        entry = corpus.get(pid)
        if not entry:
            missing += len(rows)
            continue
        items = [{"rid": rid, "snippet": snippet} for rid, snippet, _s in rows if snippet.strip()]
        scores = daemon.classify(entry.get("prompt", ""), items) or {}
        for rid, _snippet, score in rows:
            if scores.get(rid) is None:
                missing += 1
            else:
                scored.append((scores[rid], score))
    tau, grid = choose_tau(scored)
    out = {"index": args.index, "tau": tau, "pairs": len(scored), "missing": missing,
           "daemon_failures": daemon.failures, "grid": grid,
           "base_strict": round(sum(1 for _c, s in scored if s == 2) / len(scored), 3)}
    (r4 / f"tau_{args.index}.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))
    return 0 if not daemon.failures else 1


def cmd_run(args, r4):
    corpus = _load_corpus(args.data)
    entries = test_set(corpus.values(), used_pids(args.data))
    tau = json.loads((r4 / f"tau_{args.index}.json").read_text(encoding="utf-8"))["tau"]
    daemon = Daemon()
    (r4 / "runs").mkdir(parents=True, exist_ok=True)
    groups = {g: sum(1 for e in entries if e["group"] == g) for g in ("start", "cont")}
    print(f"T: {len(entries)} Prompts {groups} | Index {args.index} | tau {tau}")
    for name in args.variants.split(","):
        rows = run_variant(name, entries, daemon.search, daemon.classify, tau)
        with open(r4 / "runs" / f"{args.index}__{name}.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        with_items = sum(1 for r in rows if r["items"])
        print(f"{args.index}__{name}: {len(rows)} Prompts, {with_items} mit Material, "
              f"{sum(len(r['items']) for r in rows)} Items")
    print(f"Daemon-Aufrufe: {daemon.calls}, Fehler: {daemon.failures}")
    return 0 if not daemon.failures else 1


def cmd_prepare(args, r4):
    corpus = _load_corpus(args.data)
    pairs = collect_pairs(_load_runs(r4), corpus)
    jdir = r4 / "judge"
    jdir.mkdir(parents=True, exist_ok=True)
    verdicts, _bad = _load_verdicts(r4, set(pairs))
    todo = sorted(p for p in pairs if p not in verdicts)
    random.Random(args.seed).shuffle(todo)
    batches = [todo[i:i + args.batch_size] for i in range(0, len(todo), args.batch_size)]
    start = len(list(jdir.glob("batch_*.jsonl"))) + 1
    for i, batch in enumerate(batches, start):
        with open(jdir / f"batch_{i:02d}.jsonl", "w", encoding="utf-8") as fh:
            for key in batch:
                fh.write(json.dumps(pairs[key], ensure_ascii=False) + "\n")
    print(f"Paare gesamt: {len(pairs)} | schon bewertet: {len(verdicts)} | "
          f"neue Batches: {len(batches)} ({len(todo)} Paare), ab batch_{start:02d}")
    return 0


def cmd_score(args, r4):
    corpus = _load_corpus(args.data)
    runs = _load_runs(r4)
    pairs = collect_pairs(runs, corpus)
    verdicts, bad = _load_verdicts(r4, set(pairs))
    print(f"Urteile: {len(set(verdicts) & set(pairs))}/{len(pairs)} | ungültige Zeilen: {len(bad)}")
    summary = {}
    for group, label in (("start", "Session-Starts"), ("cont", "Fortsetzungen"), (None, "ganz T")):
        print(f"\n[{label}]")
        print("  Lauf        n  material  useful  noise  prec2  prec1  items  offen")
        for name, rows in runs.items():
            m = score_rows(rows, verdicts, group)
            summary.setdefault(name, {})[group or "all"] = m
            print(f"  {name:<10} {m['n']:>3} {m['material_rate']:>8.0%} {m['useful_rate']:>7.0%} "
                  f"{m['noise_rate']:>6.0%} {m['precision_strict']:>6.0%} "
                  f"{m['precision_lenient']:>6.0%} {m['items']:>6} {m['unjudged']:>6}")
    print("\n[Kriterien, vorregistriert: useful >= +10 pp, noise <= +5 pp]")
    checks = [("Paket B", "I0__B", "I0__V0", "start"),
              ("Paket A1", "I1__B", "I0__B", "all"),
              ("Paket A2", "I2__B", "I0__B", "all")]
    for label, cand, base, group in checks:
        if cand in summary and base in summary:
            c, b = summary[cand][group], summary[base][group]
            ok = passes(c, b)
            summary.setdefault("_criteria", {})[label] = ok
            print(f"  {label}: {cand} gegen {base} auf {group}: "
                  f"useful {b['useful_rate']:.0%} -> {c['useful_rate']:.0%}, "
                  f"noise {b['noise_rate']:.0%} -> {c['noise_rate']:.0%} => "
                  f"{'ERFÜLLT' if ok else 'nicht erfüllt'}")
    print("\n[Treffsicherheit je Topf: n, Note 2, Note >= 1]")
    for name, rows in runs.items():
        parts = partition_precision(rows, verdicts)
        text = ", ".join(f"{p} {v['n']}/{v['strict']}/{v['lenient']}" for p, v in sorted(parts.items()))
        summary[name]["partitions"] = parts
        print(f"  {name:<10} {text}")
    (r4 / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Runde 4: Töpfe und Index-Varianten")
    ap.add_argument("cmd", choices=["fit-tau", "run", "prepare", "score"])
    ap.add_argument("--data", required=True, help="Datenordner mit corpus.jsonl")
    ap.add_argument("--index", default="I0", help="Kürzel des Index-Builds (I0, I1, I2)")
    ap.add_argument("--daemon-url", help="Daemon dieses Index-Builds")
    ap.add_argument("--variants", default="B")
    ap.add_argument("--batch-size", type=int, default=125)
    ap.add_argument("--seed", type=int, default=4)
    args = ap.parse_args(argv)
    if args.daemon_url:
        os.environ["ATLAS_DAEMON_URL"] = args.daemon_url
    r4 = Path(args.data) / "r4"
    r4.mkdir(parents=True, exist_ok=True)
    return {"fit-tau": cmd_fit_tau, "run": cmd_run,
            "prepare": cmd_prepare, "score": cmd_score}[args.cmd](args, r4)


if __name__ == "__main__":
    raise SystemExit(main())
