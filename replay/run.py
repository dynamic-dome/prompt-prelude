# -*- coding: utf-8 -*-
"""Replay-Runner: Material-Varianten gegen denselben Prompt-Korpus fahren.

Pro Korpus-Eintrag und Variante: Query bauen, `/search` (k=12, wie der Hook),
Partitionierung exakt wie `prompt_prelude.lookup_sources` (atlas/-Caps top 3,
Mentoren mit Präfix-Allowlist + Overlap-Gate). Varianten-Logik stammt aus
`prompt_prelude` selbst — gemessen wird, was der Hook übernehmen würde.
Plan + vorregistriertes Kriterium: docs/2026-09-28-plan-relevanz-replay.md.
"""
import json
import random
from pathlib import Path

import prompt_prelude as pp

VARIANTS = {
    "V0": {"project": False, "context": False},   # v10-Baseline
    "V1": {"project": True, "context": False},    # Projekt-Anker (Idee 1)
    "V2": {"project": False, "context": True},    # Gesprächskontext (Idee 2)
    "V3": {"project": True, "context": True},     # beides
}
SEARCH_K = 12
CAPS_LIMIT = 3
NWS_MIN_LEN = 40  # no_work_signal-Stichprobe: nur Prompts mit Substanz


def variant_query(entry, context):
    base = pp.extract_query(entry.get("prompt", ""))
    if not context:
        return base
    extra = pp.context_terms(entry.get("prev_assistant"), prompt_terms=base)
    return " ".join([base] + extra).strip()


def _rid_of_hint(hint):
    return str(hint).split(" — ", 1)[0]


def select_items(results, terms, slugs, project_results=None, limit=CAPS_LIMIT):
    """-> [{rid, hint, snippet, partition}] in Injektions-Reihenfolge.
    Caps + Mentoren exakt wie der v10-Hook; mit Slugs zusätzlich die
    Projekt-Partition aus der zweiten Suche (nur neue record_ids)."""
    ranked = list(results or [])
    by_rid = {str((r or {}).get("record_id", "")): r for r in ranked if isinstance(r, dict)}
    items = []
    caps = [r for r in ranked
            if isinstance(r, dict) and str(r.get("record_id", "")).startswith("atlas/")]
    for r in caps[:limit]:
        hint = pp.format_cap_hint(r)
        if hint:
            items.append({"rid": str(r.get("record_id")), "hint": hint,
                          "snippet": str(r.get("snippet") or "")[:300], "partition": "caps"})
    for hint in pp.filter_mentor_results(ranked, terms):
        rid = _rid_of_hint(hint)
        items.append({"rid": rid, "hint": hint,
                      "snippet": str((by_rid.get(rid) or {}).get("snippet") or "")[:300],
                      "partition": "mentor"})
    if slugs and project_results:
        seen = {i["rid"] for i in items}
        for r in pp.select_project_hits(project_results, slugs, exclude=seen):
            hint = pp.format_cap_hint(r)
            if hint:
                items.append({"rid": str(r.get("record_id")), "hint": hint,
                              "snippet": str(r.get("snippet") or "")[:300],
                              "partition": "project"})
    return items


def gate_of(prompt):
    """Gate-Entscheidung des Hooks VOR der Suche (validiert: 836/836 Korpus-
    Prompts stimmen mit der v9-Live-Telemetrie überein)."""
    skip, reason = pp.should_skip(prompt)
    if skip:
        return reason
    if pp.detect_work_signals(prompt) or pp.match_phase(prompt)[0] == "planning":
        return "pass"
    return "no_work_signal"


def stratified_sample(entries, n_pass, n_nws, seed):
    """Alle Gate-Pass-Prompts (bzw. n_pass davon) + n_nws substanzielle
    no_work_signal-Prompts; deterministisch per Seed."""
    rng = random.Random(seed)
    tagged = [dict(e, gate=gate_of(e.get("prompt", ""))) for e in entries]
    passed = [e for e in tagged if e["gate"] == "pass"]
    nws = [e for e in tagged
           if e["gate"] == "no_work_signal" and len(e.get("prompt", "")) >= NWS_MIN_LEN]
    pick = lambda pool, n: pool if len(pool) <= n else rng.sample(pool, n)
    return pick(passed, n_pass) + pick(nws, n_nws)


def run_replay(entries, variants, search_fn):
    """-> {variant: [{pid, gate, terms, slugs, search_ok, items}]}.
    search_fn(terms) -> results-Liste oder None; Ergebnisse je Query gecacht."""
    cache = {}

    def cached(q):
        if q not in cache:
            cache[q] = search_fn(q) if q else []
        return cache[q]

    out = {name: [] for name in variants}
    for e in entries:
        for name, cfg in variants.items():
            terms = variant_query(e, cfg["context"])
            results = cached(terms)
            slugs = pp.project_slugs(e.get("cwd")) if cfg["project"] else []
            pq = pp.project_query(terms, slugs)
            project_results = cached(pq) if pq else None
            out[name].append({"pid": e["pid"], "gate": e.get("gate"), "terms": terms,
                              "slugs": slugs, "search_ok": results is not None,
                              "items": select_items(results, terms, slugs,
                                                    project_results=project_results)})
    return out


def daemon_search(terms, timeout=10.0):
    """Echter /search-Call; großzügiges Timeout — der Replay misst Relevanz,
    nicht Kaltstart-Latenz (die ist Idee 8)."""
    return pp.search_via_daemon(terms, SEARCH_K, timeout=timeout)


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Replay der Material-Varianten")
    ap.add_argument("--corpus", default=str(here / "data" / "corpus.jsonl"))
    ap.add_argument("--out-dir", default=str(here / "data" / "runs"))
    ap.add_argument("--n-pass", type=int, default=140)
    ap.add_argument("--n-nws", type=int, default=60)
    ap.add_argument("--seed", type=int, default=12)
    args = ap.parse_args(argv)
    entries = [json.loads(l) for l in open(args.corpus, encoding="utf-8") if l.strip()]
    sample = stratified_sample(entries, args.n_pass, args.n_nws, args.seed)
    runs = run_replay(sample, VARIANTS, daemon_search)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for name, rows in runs.items():
        with open(out_dir / f"{name}.jsonl", "w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        failed = sum(1 for r in rows if not r["search_ok"])
        with_items = sum(1 for r in rows if r["items"])
        print(f"{name}: {len(rows)} Prompts, {with_items} mit Material, "
              f"{failed} Suchfehler")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
