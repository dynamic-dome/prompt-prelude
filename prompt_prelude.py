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
