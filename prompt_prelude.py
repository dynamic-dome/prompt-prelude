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


ATLAS_ROOT_DEFAULT = r"C:\Users\domes\AI\agent-memory-atlas\.atlas-index"

STOP_WORDS = {"ich", "du", "wir", "das", "die", "der", "ein", "eine", "ist", "sind",
              "hab", "habe", "bitte", "kannst", "mich", "mir", "wie", "was", "warum",
              "machen", "kann", "soll", "beim", "wenn", "dann", "auch", "noch", "mal"}


def extract_query(prompt):
    low = prompt.lower()
    domains = [d for d, kws in DOMAIN_HINTS.items() if any(kw in low for kw in kws)]
    tokens = re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", prompt)
    tokens = [t for t in tokens if t.lower() not in STOP_WORDS]
    return " ".join((domains + tokens[:8])[:12])


def find_atlas_db(atlas_root):
    try:
        cur = os.path.join(atlas_root, "CURRENT.json")
        with open(cur, encoding="utf-8") as f:
            active = json.load(f)["active_path"]
        db = os.path.join(atlas_root, active, "bm25.db")
        return db if os.path.exists(db) else None
    except Exception:
        return None


def query_atlas(terms, db_path, limit=3):
    if not terms.strip() or not db_path:
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute(
                "SELECT record_id FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (terms, limit),
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


def run(payload, *, atlas_root, state_dir, log_path, now):
    prompt = payload.get("prompt", "") or ""
    session_id = payload.get("session_id", "default") or "default"

    skip, reason = should_skip(prompt)
    if skip:
        log_telemetry({"t": now, "skip": reason, "session": session_id}, log_path)
        return ""

    domain = detect_domain(prompt)
    phase = detect_phase(prompt)
    routing = build_rag_routing(domain, phase)

    if not routing:
        log_telemetry({"t": now, "skip": "no_routing", "session": session_id}, log_path)
        return ""

    key = dedupe_key(domain, phase)
    fired = load_fired(session_id, state_dir)
    if key in fired:
        log_telemetry({"t": now, "skip": "deduped", "key": key, "session": session_id}, log_path)
        return ""

    caps = query_atlas(extract_query(prompt), find_atlas_db(atlas_root))
    ctx = compose_context(domain, phase, routing, caps)
    out = make_output(ctx)

    if out:
        fired.add(key)
        save_fired(session_id, state_dir, fired)
        log_telemetry({"t": now, "fired": True, "domain": domain, "phase": phase,
                       "key": key, "caps": caps, "session": session_id}, log_path)
    return out


def main():
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        out = run(
            payload,
            atlas_root=ATLAS_ROOT_DEFAULT,
            state_dir=os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe"),
            log_path=os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_prelude.jsonl"),
            now=time.time(),
        )
        if out:
            print(out)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
