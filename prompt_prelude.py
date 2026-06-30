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


DOMAIN_ROUTING = {
    "ui-frontend":   "Durchsuche das Capability-RAG (memory_search) nach UI-/Design-Skills "
                     "(z.B. frontend-design, modern-web-design), bevor du einen Ansatz festlegst.",
    "data-analysis": "Prüfe das Capability-RAG nach Daten-Viz-Skills (z.B. d3js-visualization) "
                     "und passenden Auswertungs-Patterns.",
    "workflow":      "Prüfe das Capability-RAG nach Orchestrierungs-/Workflow-Skills und Multi-Agent-Patterns.",
    "debug":         "Ziehe systematic-debugging / diagnose-hitl in Betracht und reproduziere, bevor du fixt.",
    "research":      "Prüfe die NotebookLM-Registry und deep-research, bevor du aus dem Gedächtnis antwortest.",
    "code-impl":     "Prüfe kurz, ob ein passender Skill oder ein Pattern im Capability-RAG existiert.",
}

PLANNING_ROUTING = ("Planungsphase: Konsultiere die SE-Wissensbasis (§13) und arbeite im "
                    "Sparring-Modus (§19) — benenne aktiv Schwächen und schlage eine Definition-of-Done vor.")


def build_rag_routing(domain, phase):
    """M2-Instruktions-Zeilen je Domain + optional Planungs-Zeile."""
    lines = []
    if domain in DOMAIN_ROUTING:
        lines.append(DOMAIN_ROUTING[domain])
    if phase == "planning":
        lines.append(PLANNING_ROUTING)
    return lines
