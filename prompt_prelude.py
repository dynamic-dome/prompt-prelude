#!/usr/bin/env python3
"""UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG.
stdlib-only. Fail-soft: Fehler -> kein Output. Exit immer 0.
Opt-out: Prompt mit //raw prefixen."""
import sys, os, json, re, time


def _force_utf8(stream):
    """Stream auf UTF-8 stellen, selbst fail-soft (exotische Streams können werfen)."""
    try:
        if hasattr(stream, "reconfigure") and stream.encoding and stream.encoding.lower() != "utf-8":
            stream.reconfigure(encoding="utf-8")
    except Exception:
        pass


_force_utf8(sys.stdout)
_force_utf8(sys.stderr)

MIN_PROMPT_LEN = 30
TRIVIAL = {"ja", "nein", "ok", "okay", "danke", "bitte", "weiter", "stop",
           "ja bitte", "nein danke", "mach weiter", "passt"}


def should_skip(prompt):
    """Returns (skip: bool, reason: str).

    Reihenfolge bewusst: bekannte Füllwörter zuerst als 'trivial' klassifizieren
    (für ehrliche Telemetrie, längen-unabhängig), danach Längen-/Wort-Count-Gate
    als 'too_short'. Sonst maskiert der Längen-Check fast alle trivialen Fälle.
    """
    s = (prompt if isinstance(prompt, str) else "").strip()
    if s.lower().startswith("//raw"):
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


def compose_context(domain, phase, routing_lines, capabilities=None):
    """Baut den <prompt_prelude>-Block. Leer-String, wenn nichts Relevantes."""
    if not routing_lines and not capabilities:
        return ""
    dom = domain or "-"
    echo = (f"ECHO: Beginne deine Antwort mit genau einer Zeile: "
            f"↳ prelude · [{phase}] [{dom}] · RAG-Auftrag aktiv")
    parts = [echo, ""]
    if routing_lines:
        parts.append("RAG-AUFTRAG (weicher Hinweis, kein Befehl):")
        parts.extend(f"- {l}" for l in routing_lines)
    if capabilities:
        parts.append("")
        parts.append("MÖGLICHERWEISE RELEVANT (BM25-Treffer, optional):")
        parts.extend(f"- [{c}]" for c in capabilities)
    body = "\n".join(parts)
    return f'<prompt_prelude phase="{phase}" domain="{dom}">\n{body}\n</prompt_prelude>'


def make_output(additional_context):
    """Finales JSON gemäß Hook-Contract. Leer-String -> kein Output."""
    if not additional_context:
        return ""
    return json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    }, ensure_ascii=False)


def dedupe_key(domain, phase):
    if domain:
        return domain
    return "_planning_" if phase == "planning" else "_none_"


def load_fired(session_id, state_dir):
    try:
        p = os.path.join(state_dir, f"fired_{session_id}.json")
        with open(p, encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_fired(session_id, state_dir, fired):
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = os.path.join(state_dir, f"fired_{session_id}.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump(sorted(fired), f)
    except Exception:
        pass


def log_telemetry(record, log_path):
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass
