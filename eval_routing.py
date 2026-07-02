#!/usr/bin/env python3
"""Eval-Skript für die Threshold-Kalibrierung des semantischen Routings.

Läuft NUR manuell gegen den echten Atlas-Daemon (nicht CI, nicht Hook-Pfad):

    python eval_routing.py

Gibt pro Test-Prompt daemon-Domain (mit Top-Score + Margin) vs. keyword-Domain
als Tabelle aus. Zweck: TH_ACCEPT / TH_MARGIN / TH_CLEAR nach Datenlage
nachziehen, statt sie aus dem Bauch zu setzen. Daemon-URL via Env
ATLAS_DAEMON_URL (Default http://127.0.0.1:7801)."""
import json
import sys
import time
import urllib.request

import prompt_prelude as pp

pp._force_utf8(sys.stdout)

# ~20 repräsentative Prompts (Deutsch + Englisch), inkl. der bekannten
# Fehlklassifikations-Fälle und bewusst ambiger/Meta-Prompts.
# (label = grobe menschliche Erwartung, nur zur Orientierung beim Lesen)
EVAL_PROMPTS = [
    # bekannte Fehlklassifikations-Fälle (Substring-Ära: "ui" in build/guide)
    ("bitte den build neu starten", "none"),
    ("ein guide für git", "research/none"),
    # Meta-Fragen über Projekte (dürfen NICHT hart in eine Domain kippen)
    ("welche projekte liegen eigentlich in meinem AI ordner", "none/meta"),
    ("was haben wir gestern in der session eigentlich alles gemacht", "none/meta"),
    # echte UI-Prompts
    ("baue mir ein responsive component layout für den header", "ui-frontend"),
    ("make the landing page look more modern and clean", "ui-frontend"),
    ("die seite sieht auf dem handy total kaputt aus", "ui-frontend/debug"),
    # echte Debug-Prompts
    ("ich habe einen bug, der server crasht beim start mit einem traceback", "debug"),
    ("why does the test suite fail with a KeyError in the fixture", "debug"),
    # echte Research-Prompts
    ("recherchiere die besten embedding-modelle für deutsche texte", "research"),
    ("what is the difference between BM25 and cosine similarity", "research"),
    ("finde heraus welche sqlite fts5 tokenizer es gibt und vergleiche sie", "research"),
    # Daten-Analyse
    ("werte die csv mit den verkaufszahlen aus und mach ein chart draus", "data-analysis"),
    ("plot the monthly token costs as a bar chart per project", "data-analysis"),
    # Workflow / Orchestrierung
    ("bau einen cron-job der das wiki jede nacht synchronisiert", "workflow"),
    ("orchestrate two subagents that review each other's diffs", "workflow"),
    # Code-Implementierung
    ("implementiere eine funktion die die telemetrie-jsonl einliest und parst", "code-impl"),
    ("refactor the parser module into smaller pure functions", "code-impl"),
    ("schreib mir ein powershell skript das alte logdateien löscht", "code-impl"),
    # Füll-/Steuerprompts (sollten nirgends hart landen)
    ("mach bitte weiter mit dem nächsten schritt aus dem plan", "none"),
]


def daemon_health(timeout=2.0):
    try:
        with urllib.request.urlopen(pp._daemon_url() + "/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def main():
    health = daemon_health()
    if not health or health.get("status") != "ok":
        print(f"Atlas-Daemon nicht erreichbar unter {pp._daemon_url()}/health.")
        print("Daemon starten (bzw. ATLAS_DAEMON_URL setzen) und erneut ausführen.")
        return 1
    print(f"Daemon ok: run_id={health.get('run_id')} built_at={health.get('built_at')} "
          f"model_loaded={health.get('model_loaded')}")
    print(f"Thresholds: TH_ACCEPT={pp.TH_ACCEPT} TH_MARGIN={pp.TH_MARGIN} TH_CLEAR={pp.TH_CLEAR}")
    print()

    w_prompt = 58
    header = (f"{'prompt':<{w_prompt}} | {'daemon':<14} | {'top-score':>9} | "
              f"{'margin':>6} | {'keyword':<14} | {'erwartet':<16} | agree")
    print(header)
    print("-" * len(header))

    agree = disagree = 0
    for prompt, expected in EVAL_PROMPTS:
        t0 = time.perf_counter()
        scores = pp.classify_via_daemon(prompt, timeout=5.0)  # Eval darf langsam sein
        ms = (time.perf_counter() - t0) * 1000
        daemon_dom = pp.pick_daemon_domain(scores)
        kw_dom = pp.match_domain(prompt)[0]
        top = scores[0]["score"] if scores else 0.0
        margin = (scores[0]["score"] - scores[1]["score"]) if scores and len(scores) > 1 else 0.0
        mark = "==" if daemon_dom == kw_dom else "<>"
        agree += daemon_dom == kw_dom
        disagree += daemon_dom != kw_dom
        p = prompt if len(prompt) <= w_prompt else prompt[:w_prompt - 1] + "…"
        print(f"{p:<{w_prompt}} | {str(daemon_dom):<14} | {top:>9.3f} | "
              f"{margin:>6.3f} | {str(kw_dom):<14} | {expected:<16} | {mark} ({ms:.0f}ms)")

    print()
    print(f"Übereinstimmung daemon==keyword: {agree}/{agree + disagree} "
          f"(Abweichung ist kein Fehler — genau die Fälle von Hand bewerten "
          f"und Thresholds/Beschreibungen nachziehen).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
