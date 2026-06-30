#!/usr/bin/env python3
"""UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG.
stdlib-only. Fail-soft: Fehler -> kein Output. Exit immer 0.
Opt-out: Prompt mit //raw prefixen."""
import sys, os, json, re, time

if hasattr(sys.stdout, "reconfigure") and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

MIN_PROMPT_LEN = 30
TRIVIAL = {"ja", "nein", "ok", "okay", "danke", "bitte", "weiter", "stop",
           "ja bitte", "nein danke", "mach weiter", "passt"}


def should_skip(prompt):
    """Returns (skip: bool, reason: str).

    Reihenfolge bewusst: bekannte Füllwörter zuerst als 'trivial' klassifizieren
    (für ehrliche Telemetrie, längen-unabhängig), danach Längen-/Wort-Count-Gate
    als 'too_short'. Sonst maskiert der Längen-Check fast alle trivialen Fälle.
    """
    s = prompt.strip()
    if s.startswith("//raw"):
        return True, "raw"
    if s.lower() in TRIVIAL:
        return True, "trivial"
    if len(s) < MIN_PROMPT_LEN or len(s.split()) < 4:
        return True, "too_short"
    return False, ""


DOMAIN_HINTS = {
    "ui-frontend":   ["ui", "css", "component", "layout", "responsive", "frontend", "button", "styling"],
    "data-analysis": ["daten", "auswerten", "csv", "analyse", "chart", "viz", "dashboard", "tabelle"],
    "workflow":      ["workflow", "loop", "orchestrier", "subagent", "pipeline", "cron", "agenten"],
    "debug":         ["bug", "fehler", "crash", "traceback", "kaputt", "debug", "exception"],
    "research":      ["recherchier", "finde heraus", "quellen", "notebooklm", "was ist"],
    "code-impl":     ["funktion", "klasse", "methode", "refactor", "implementier", "skript"],
}

PLANNING_TRIGGERS = ["plane", "planung", "idee", "konzept", "wie könnte", "wie koennte",
                     "brainstorm", "überleg", "ueberleg", "architektur", "entwurf", "ansatz", "design-spec"]


def detect_domain(prompt):
    """Erste matchende Domain in Dict-Reihenfolge, sonst None."""
    low = prompt.lower()
    for domain, kws in DOMAIN_HINTS.items():
        if any(kw in low for kw in kws):
            return domain
    return None


def detect_phase(prompt):
    """Binär: planning, wenn ein Planungs-Trigger matcht; sonst quiet."""
    low = prompt.lower()
    return "planning" if any(t in low for t in PLANNING_TRIGGERS) else "quiet"
