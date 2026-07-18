#!/usr/bin/env python3
"""UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG.
stdlib-only. Fail-soft: Fehler -> kein Output. Exit immer 0.
Opt-out: Prompt mit //raw prefixen."""
import sys, os, json, re, time, hashlib
import urllib.request


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

# Harness-generierte Prompts (Subagent-Callbacks, injizierte Reminder) sind kein
# User-Intent: Routing darauf produzierte Fehl-Domains und verzerrte die
# H4-Compliance-Messung (Live-Befund 2026-07-02). Nur Prompt-ANFANG matchen —
# User-Text, der Tags bloß enthält, bleibt normal.
MACHINE_PROMPT_MARKERS = ("<task-notification>", "<system-reminder>",
                          "<local-command-stdout>", "<command-name>")


def should_skip(prompt):
    """Returns (skip: bool, reason: str).

    Reihenfolge bewusst: bekannte Füllwörter zuerst als 'trivial' klassifizieren
    (für ehrliche Telemetrie, längen-unabhängig), danach Längen-/Wort-Count-Gate
    als 'too_short'. Sonst maskiert der Längen-Check fast alle trivialen Fälle.
    """
    s = (prompt if isinstance(prompt, str) else "").strip()
    if s.lower().startswith("//raw"):
        return True, "raw"
    if s.lower().startswith(MACHINE_PROMPT_MARKERS):
        return True, "machine_prompt"
    if s.lower() in TRIVIAL:
        return True, "trivial"
    if detect_work_signals(s):
        return False, ""
    if len(s) < MIN_PROMPT_LEN or len(s.split()) < 4:
        return True, "too_short"
    return False, ""



# KOPPLUNG: muss <= TH_ACCEPT bleiben, sonst verschluckt das Praezisions-Gate
# Daemon-Routings im Band [TH_ACCEPT, PRECISION_CONFIDENCE_THRESHOLD) wieder
# (T-8-Befund 2026-07-14). Bei TH_ACCEPT-Aenderung hier mitziehen.
PRECISION_CONFIDENCE_THRESHOLD = 0.40
KEYWORD_DOMAIN_CONFIDENCE_BASE = 0.55
KEYWORD_DOMAIN_CONFIDENCE_PER_HIT = 0.15
WORK_SIGNAL_GENERAL_CONFIDENCE = 0.60

FILE_PATH_RE = re.compile(
    r"(?i)(?:[A-Z]:\\[^\s`]+|(?:\.{1,2}[\\/])?[\w.-]+[\\/][^\s`]+|"
    r"[\w.-]+\.(?:py|js|ts|tsx|jsx|md|json|ya?ml|toml|css|html|sql|sh|ps1)\b)"
)
CODE_FENCE_RE = re.compile(r"```")
INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
TASK_VERB_RE = re.compile(
    r"(?i)\b(?:implement(?:iere|ieren|ier|e|en)?|fix(?:e|en)?|debug(?:ge|gen)?|"
    r"baue|bau|build|add|write|edit|update|refactor(?:e|en)?|test(?:e|en)?|"
    r"run|create|delete|patch(?:e|en)?|style|verbessere|überarbeit(?:e|en)?|ueberarbeit(?:e|en)?|"
    r"schreib(?:e|en)?|ändere|aendere|"
    r"ergänze|ergaenze|starte|erstelle|prüfe|pruefe|"
    # T-8 2026-07-14: Research-/Review-Verben fehlten — echte Auftraege wie
    # "recherchiere ..." / "reviewe ..." liefen als no_work_signal-Skip.
    r"recherchier(?:e|en)?|review(?:e|en)?|vergleich(?:e|en)?|"
    r"analysier(?:e|en)?|untersuch(?:e|en)?|evaluier(?:e|en)?)\b"
)


def detect_work_signals(prompt):
    """Return concrete work-signal labels; fail-soft and conservative."""
    text = prompt if isinstance(prompt, str) else ""
    signals = []
    if FILE_PATH_RE.search(text):
        signals.append("file_path")
    if CODE_FENCE_RE.search(text) or INLINE_CODE_RE.search(text):
        signals.append("code")
    if TASK_VERB_RE.search(text):
        signals.append("task_verb")
    return signals


def domain_confidence(domain, routing_source, daemon_scores, keyword_hits, work_signals):
    """Small confidence scalar for the precision gate; never throws."""
    try:
        if routing_source == "daemon" and daemon_scores:
            for score in daemon_scores:
                if score.get("name") == domain:
                    return float(score.get("score", 0.0))
            return 0.0
        if routing_source == "keywords" and domain:
            return min(1.0, KEYWORD_DOMAIN_CONFIDENCE_BASE +
                       KEYWORD_DOMAIN_CONFIDENCE_PER_HIT * len(keyword_hits or []))
        if routing_source == "fallback" and domain == "general" and work_signals:
            return WORK_SIGNAL_GENERAL_CONFIDENCE
        return 0.0
    except Exception:
        return 0.0

# Keyword-Konvention: Wortgrenzen-Match (\b...\b). Ein trailing "*" macht das
# Keyword zum Präfix-Stem (\bstem...), z.B. "implementier*" matcht "implementieren".
# Substring-Matching ist verboten (Live-Befund: "ui" matchte "build"/"guide"/"quiet"
# -> ui-frontend 34/51 Domain-Events überrepräsentiert).
DOMAIN_HINTS = {
    "ui-frontend":   ["ui", "css", "component*", "layout*", "responsive", "frontend*",
                      "button*", "styling", "oberfläche", "oberflaeche", "interface*"],
    "data-analysis": ["daten", "auswert*", "csv", "analyse", "chart*", "viz", "dashboard*", "tabelle*"],
    # debug VOR workflow: match_domain ist first-match-wins in Dict-Reihenfolge,
    # und Debug-Signale (exception, traceback, crash) müssen die breiten
    # Agent-Tooling-Wörter (hook*, skill*) schlagen — "debugge einen react hook
    # mit exception" ist debug, nicht workflow (Codex-Verifier-Finding).
    "debug":         ["bug*", "fehler*", "crash*", "traceback*", "kaputt*", "debug*", "exception*"],
    "workflow":      ["workflow*", "loop*", "orchestrier*", "subagent*", "pipeline*", "cron*", "agenten",
                      # Agent-Tooling (Iteration 1): Hook-/Skill-/MCP-Arbeit lief als no_routing
                      "hook*", "skill*", "mcp*"],
    "research":      ["recherchier*", "recherche", "finde heraus", "quellen", "notebooklm", "was ist"],
    "code-impl":     ["funktion*", "klasse", "klassen", "methode*", "refactor*", "implementier*", "skript*"],
}

PLANNING_TRIGGERS = ["plane", "planen", "planung", "idee*", "konzept*", "wie könnte", "wie koennte",
                     "brainstorm*", "überleg*", "ueberleg*", "architektur*", "entwurf*", "ansatz",
                     "ansätze", "ansaetze", "design-spec",
                     # NOTES-live-findings Befund 2: fehlende Planungs-/Machbarkeits-Trigger
                     "durchspielen", "klären", "klaeren", "machbar*", "durchführbar*",
                     "durchfuehrbar*", "feasibility", "grundgerüst*", "grundgeruest*",
                     "hülle", "huelle"]

_KW_RE_CACHE = {}


def _kw_regex(kw):
    """Kompiliertes Wortgrenzen-Pattern für ein Keyword (gecacht, Hot-Path-billig)."""
    pat = _KW_RE_CACHE.get(kw)
    if pat is None:
        if kw.endswith("*"):
            pat = re.compile(r"\b" + re.escape(kw[:-1]))
        else:
            pat = re.compile(r"\b" + re.escape(kw) + r"\b")
        _KW_RE_CACHE[kw] = pat
    return pat


def match_domain(prompt):
    """(domain, matched_keywords) — erste matchende Domain in Dict-Reihenfolge, sonst (None, [])."""
    low = prompt.lower()
    for domain, kws in DOMAIN_HINTS.items():
        hits = [kw.rstrip("*") for kw in kws if _kw_regex(kw).search(low)]
        if hits:
            return domain, hits
    return None, []


def detect_domain(prompt):
    """Erste matchende Domain in Dict-Reihenfolge, sonst None."""
    return match_domain(prompt)[0]


def match_phase(prompt):
    """(phase, matched_triggers) — planning, wenn ein Planungs-Trigger matcht; sonst quiet."""
    low = prompt.lower()
    hits = [t.rstrip("*") for t in PLANNING_TRIGGERS if _kw_regex(t).search(low)]
    return ("planning" if hits else "quiet"), hits


def detect_phase(prompt):
    """Binär: planning, wenn ein Planungs-Trigger matcht; sonst quiet."""
    return match_phase(prompt)[0]


DOMAIN_ROUTING = {
    "ui-frontend":   "Durchsuche das Capability-RAG (memory_search) nach UI-/Design-Skills "
                     "(z.B. frontend-design, modern-web-design), bevor du einen Ansatz festlegst.",
    "data-analysis": "Prüfe das Capability-RAG nach Daten-Viz-Skills (z.B. d3js-visualization) "
                     "und passenden Auswertungs-Patterns.",
    "workflow":      "Prüfe das Capability-RAG nach Orchestrierungs-/Workflow-Skills und Multi-Agent-Patterns.",
    "debug":         "Starte mit systematic-debugging bzw. diagnose-hitl und reproduziere den Fehler, bevor du fixt.",
    "research":      "Prüfe die NotebookLM-Registry und deep-research, bevor du aus dem Gedächtnis antwortest.",
    "code-impl":     "Durchsuche das Capability-RAG (memory_search) nach passenden Skills/Patterns, bevor du implementierst.",
    # Iteration 2 (v3): breiter Fallback. Der Compliance-Eval (2026-07-03) zeigte,
    # dass der Advisory-Kanal als ANWEISUNG ~3% wirkt — der Wert liegt in der
    # Vorab-Injektion der Caps. Darum feuert jetzt JEDER substantielle Prompt
    # ohne Spezial-Domain als 'general' und zieht dieselbe Caps-Vorabsuche.
    "general":       "Prüfe das Capability-RAG (memory_search_tool) nach passenden Skills/Fähigkeiten "
                     "oder Stack-Wissen, bevor du antwortest — die Vorab-Treffer unten sind schon gesucht.",
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


def compose_context(domain, phase, routing_lines, capabilities=None, query=None, mentors=None):
    """Baut den <prompt_prelude>-Block. Leer-String, wenn nichts Relevantes.

    Wording-Politik (H1, Iteration 1): keine Selbst-Entwertung ("kein Befehl",
    "optional") — der Funnel deckelt die Frequenz bereits, die Sprache darf
    imperativ sein. Caps sind ein VORGEZOGENES Suchergebnis (lesen statt selbst
    suchen); `query` liefert den fertigen memory_search_tool-Einstieg zum Vertiefen.

    Die ECHO-Zeile (erzwungene erste Antwortzeile) war Rollout-Verifikation und
    ist nur noch mit Env PRELUDE_ECHO=1 aktiv (Default: aus)."""
    if not routing_lines and not capabilities and not mentors:
        return ""
    dom = domain or "-"
    parts = []
    if os.environ.get("PRELUDE_ECHO") == "1":
        parts.append(f"ECHO: Beginne deine Antwort mit genau einer Zeile: "
                     f"↳ prelude · [{phase}] [{dom}] · RAG-Auftrag aktiv")
        parts.append("")
    if routing_lines:
        parts.append("RAG-AUFTRAG (vor dem ersten Arbeitsschritt erledigen):")
        parts.extend(f"- {l}" for l in routing_lines)
        if query:
            parts.append(f'- Vertiefung bei Bedarf: memory_search_tool("{query}")')
    if capabilities:
        parts.append("")
        parts.append("VORAB-SUCHE Capability-RAG (bereits ausgeführt — prüfe diese Treffer zuerst):")
        parts.extend(f"- [{c}]" for c in capabilities)
    if mentors:
        parts.append("")
        parts.append("VORAB-SUCHE Frühere Fälle (bereits ausgeführt — ähnliche gelöste "
                     "Aufgaben/Session-Notes, bei Bedarf nachlesen):")
        parts.extend(f"- [{m}]" for m in mentors)
    body = "\n".join(parts)
    return f'<prompt_prelude phase="{phase}" domain="{dom}">\n{body}\n</prompt_prelude>'


def build_system_message(domain, phase, caps, caps_source, mentors=None):
    """Sichtbare Status-Zeile für den USER (natives systemMessage-Feld, in den
    Claude-Code-Docs 'shown to the user'). Der additionalContext geht nur in
    Claudes Kontext — DAS hier ist der einzige Kanal, den der Mensch am Schirm
    sieht. Kompakt: was hat der Hook entschieden + wie viele Caps vorab gesucht.
    Das mentor-Segment (v7) erscheint nur bei Treffern — ohne Ghost-Mentor-Hits
    bleibt das Zeilenformat exakt rückwärtskompatibel."""
    n = len(caps or [])
    msg = f"prelude ▸ {domain or '-'} · {phase} · caps={n}({caps_source or 'none'})"
    m = len(mentors or [])
    return msg + (f" · mentor={m}" if m else "")


def make_output(additional_context, system_message=None):
    """Finales JSON gemäß Hook-Contract. Leer-String + keine systemMessage -> kein Output.

    additionalContext -> Claudes Kontext (unsichtbar für den User).
    systemMessage     -> sichtbare Zeile beim User (nur beim Feuern gesetzt)."""
    if not additional_context and not system_message:
        return ""
    out = {}
    if additional_context:
        out["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    if system_message:
        out["systemMessage"] = system_message
    return json.dumps(out, ensure_ascii=False)


def make_skip_status(reason, domain=None, phase=None):
    """Sichtbare Skip-Zeile (systemMessage-only, KEIN additionalContext).

    Live-Befund 2026-07-09: nach dem B2-Präzisions-Gate fühlte sich der Hook
    tot an — kein Feuern, keine Zeile, keine Bestätigung, dass er überhaupt
    lief. Darum sieht der User jetzt auf jedem substanziellen Prompt die
    Entscheidung; Claudes Kontext bleibt unberührt (kein hookSpecificOutput).
    Nur für post-classify-Skips gedacht — raw/trivial/too_short/machine_prompt
    bleiben komplett still (Konversations-/Maschinen-Rauschen)."""
    detail = f" · {domain}" if domain else ""
    if domain and phase and phase != "quiet":
        detail += f":{phase}"
    return make_output(None, system_message=f"prelude ▸ skip · {reason}{detail}")


def topic_signature(prompt):
    """Kurzer stabiler Hash der signifikanten Tokens eines Prompts.

    v3-Dedupe: statt 1x pro domain:phase pro Session (fühlte sich tot an, weil
    nach dem ersten Feuern Stille herrschte) feuert jetzt jedes NEUE Thema wieder,
    nur exakte Themen-Wiederholung bleibt still. Order-unabhängig (sortierte
    Token-Menge), damit 'baue X layout' == 'layout baue X'."""
    text = prompt if isinstance(prompt, str) else ""   # fail-soft: nie auf Nicht-String .lower()
    toks = sorted(set(re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", text.lower())) - STOP_WORDS)
    if not toks:
        return "0"
    return hashlib.sha1(" ".join(toks).encode("utf-8")).hexdigest()[:8]


def dedupe_key(domain, phase, topic_sig=None):
    """Key domain:phase:topic — quiet->planning derselben Domain feuert erneut
    (Live-Befund 1), und ein neues Thema (topic_sig) feuert ebenfalls erneut (v3).
    Ohne topic_sig bleibt der Key rückwärtskompatibel domain:phase."""
    if domain:
        base = f"{domain}:{phase}"
        return f"{base}:{topic_sig}" if topic_sig else base
    return "_planning_" if phase == "planning" else "_none_"


# Expliziter RAG-/Skill-Bezug im Prompt re-armt den Dedupe (feuert trotz Key-Treffer).
RAG_REARM_TRIGGERS = ["welche skills", "memory_search", "capability", "capabilities",
                      "fähigkeiten", "faehigkeiten", "ins rag", "rag-suche",
                      "semantische suche", "skill-suche"]


def has_rag_reference(prompt):
    """True, wenn der Prompt explizit nach RAG/Skills/Capabilities fragt."""
    low = prompt.lower()
    return any(t in low for t in RAG_REARM_TRIGGERS)


def _safe_session(session_id):
    """Sanitize gegen Pfad-Traversal: nur [A-Za-z0-9_-], max 64 Zeichen."""
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", str(session_id))[:64]
    return safe or "default"


def _fired_path(session_id, state_dir):
    return os.path.join(state_dir, f"fired_{_safe_session(session_id)}.json")


def load_fired(session_id, state_dir):
    try:
        with open(_fired_path(session_id, state_dir), encoding="utf-8") as f:
            return set(json.load(f))
    except Exception:
        return set()


def save_fired(session_id, state_dir, fired):
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = _fired_path(session_id, state_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(fired), f)
        os.replace(tmp, p)  # atomar, gegen Teil-Schreib-Races
    except Exception:
        pass


def cleanup_state(state_dir, now, max_age_days=7):
    """Dedupe-Dateien älter als max_age_days löschen. Fail-soft, nie werfen."""
    try:
        cutoff = now - max_age_days * 86400
        with os.scandir(state_dir) as it:
            for entry in it:
                try:
                    if entry.is_file() and entry.stat().st_mtime < cutoff:
                        os.unlink(entry.path)
                except Exception:
                    pass
    except Exception:
        pass


# Schema-Version an jedem Event: v7 = Ghost-Mentor (zweite Vorab-Suche-
# Partition "Frühere Fälle" aus haupt-wiki/queries/, summary-harvest/,
# agent-memory/; neue fired-Felder mentor/mentor_count/mentor_source;
# Injektions-Semantik geändert -> Compliance-/Routing-Auswertungen nie mit
# v6 mischen). v6 = Threshold-Kalibrierung T-8 (TH_ACCEPT
# 0.45->0.40, TH_CLEAR 0.50->0.45; Daemon-Routing-Population geändert). Achtung:
# v5 ist intern INHOMOGEN — v5a bis 2026-07-07 22:35 (vor T-30), v5b danach
# (Präzisions-Gate T-30 + Skip-Zeile T-31 liefen ohne Bump; fired-Population
# durch no_work_signal-Skips verschoben). v5 = Caps-Gating atlas/-only plus
# Query-Cleanup (Caps-Semantik geändert: A/B-Daten NIE über v4/v5 mischen). v4 = stdin-
# UTF-8-Fix: alle Events davor sind Mojibake-vergiftet — Umlaute kamen als
# cp1252-Bytes an, 0/208 v3-Events mit korrekten Umlauten. v3 = Iteration 2
# (general-Fallback: breit feuern, sichtbare systemMessage-Zeile, Dedupe pro
# Thema). v2 = Iteration 1 (imperatives Wording, machine_prompt-Skip). v1 =
# ohne "v". Auswertungen NIE über Versionen mischen.
TELEMETRY_SCHEMA_VERSION = 7


def log_telemetry(record, log_path):
    try:
        record.setdefault("v", TELEMETRY_SCHEMA_VERSION)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass



def log_decision(record, decision_log_path):
    try:
        with open(decision_log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def decision_record(decision, reason, *, now, session_id, prompt_preview,
                    classification=None, work_signals=None):
    return {
        "t": now,
        "decision": decision,
        "reason": reason,
        "session": session_id,
        "prompt_preview": prompt_preview,
        "classification": classification or {},
        "work_signals": work_signals or [],
    }

ATLAS_ROOT_DEFAULT = r"C:\Users\domes\AI\agent-memory-atlas\.atlas-index"

STOP_WORDS = {"ich", "du", "wir", "das", "die", "der", "ein", "eine", "ist", "sind",
              "hab", "habe", "bitte", "kannst", "mich", "mir", "wie", "was", "warum",
              "machen", "kann", "soll", "beim", "wenn", "dann", "auch", "noch", "mal",
              "einfach", "jetzt", "gerne", "eigentlich", "vielleicht", "okay", "sagen",
              "schauen", "erstmal", "wirklich", "vielen", "dank", "danke", "super",
              "würde", "wuerde", "möchte", "moechte", "sollte", "irgendwie", "quasi"}


def extract_query(prompt):
    """Nur Content-Tokens (Stopwörter raus, max 12). Seit v5 werden die
    Domain-LABELS nicht mehr vorangestellt — Label-Namen sind keine
    Suchbegriffe und verzerrten BM25 wie Embedding (Live-Telemetrie:
    "data-analysis workflow research …"-Queries). Content-Wörter wie
    "frontend" oder "workflow" bleiben bewusst erhalten: sie sind
    hochsignifikante Suchbegriffe, nur das Voranstellen war das Problem."""
    tokens = re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", str(prompt))
    tokens = [t for t in tokens if t.lower() not in STOP_WORDS]
    return " ".join(tokens[:12])


def find_atlas_db(atlas_root):
    try:
        cur = os.path.join(atlas_root, "CURRENT.json")
        with open(cur, encoding="utf-8") as f:
            active = json.load(f)["active_path"]
        db = os.path.join(atlas_root, active, "bm25.db")
        return db if os.path.exists(db) else None
    except Exception:
        return None


def build_fts_query(terms):
    """FTS5-sichere Query: Bindestriche zu getrennten Termen, jeder Term
    doppelt-gequotet, OR-verknüpft. Ein rohes 'ui-frontend' warf vorher
    OperationalError 'no such column: frontend' -> caps war in 16/16 Live-Fires
    leer. ANDed Tokens matchen bei bis zu 12 Termen praktisch nie -> OR ist Pflicht."""
    tokens = [t for t in re.split(r"[\s\-]+", terms) if t]
    return " OR ".join('"{}"'.format(t.replace('"', '""')) for t in tokens)


def _query_atlas_filtered(terms, db_path, limit=3, raw_limit=12):
    if not str(terms).strip() or not db_path:
        return [], 0
    fts = build_fts_query(str(terms))
    if not fts:
        return [], 0
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=0.5)
        try:
            raw_rows = conn.execute(
                "SELECT record_id FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (fts, int(raw_limit)),
            ).fetchall()
            rows = conn.execute(
                "SELECT record_id FROM chunks WHERE chunks MATCH ? AND record_id LIKE 'atlas/%' ORDER BY rank LIMIT ?",
                (fts, int(limit)),
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows], len(raw_rows)
    except Exception:
        return [], 0


def query_atlas(terms, db_path, limit=3):
    return _query_atlas_filtered(terms, db_path, limit=limit)[0]


# ---------------------------------------------------------------------------
# Semantisches Routing über den Atlas-HTTP-Daemon (fail-soft, stdlib-only).
# Vertrag (eingefroren):
#   GET  /health   -> {"status":"ok","run_id":...,"built_at":...,"model_loaded":bool}
#   POST /classify {"query": str, "labels":[{"name","description"},...]}
#                  -> {"scores":[{"name","score"},...]} absteigend (Cosine)
#   POST /search   {"query": str, "k": int}
#                  -> {"built_at":..., "results":[{record_id, source_path,
#                      abs_path, snippet/heading, score},...]}
# ---------------------------------------------------------------------------

ATLAS_DAEMON_URL_DEFAULT = "http://127.0.0.1:7801"
ATLAS_DAEMON_TIMEOUT_DEFAULT = 0.5   # Sekunden; klein, Hook hat 2s-Gesamtbudget
DAEMON_BUDGET_S = 1.2                # Gesamt-Guard: alle Daemon-Calls eines Laufs zusammen
CLASSIFY_PROMPT_CAP = 500            # Query-Kappung für /classify

# Threshold-Politik (alle drei sind Kalibrierungs-Kandidaten, via eval_routing.py
# gegen echte Prompts nachziehen). Kalibriert 2026-07-02 gegen die 20-Prompt-Eval:
# 0.35 liess "build neu starten"->code-impl (0.360) und "guide für git" (0.373)
# durch; 0.42 killt beide und behält die semantischen Gewinne (debug 0.466/0.520).
# T-8-Kalibrierung 2026-07-14 auf 181 Live-Decisions mit daemon_top (v5b):
# akzeptierte Daemon-Routings hatten min-Score 0.46, fallback-Median lag bei
# 0.29 — 0.45 lehnte plausible 0.40-0.47-Grenzfälle ab (Stichprobe manuell
# gesichtet). 0.40/0.45 hebt Daemon-Routings ~+20% (35->42); Fehlrouting-Kosten
# sind niedrig, weil das atlas/-Caps-Gate Junk ohnehin auf 0 filtert.
TH_ACCEPT = 0.40  # Mindest-Score des Bestplatzierten
TH_MARGIN = 0.05  # Mindestabstand zum Zweitplatzierten
TH_CLEAR = 0.45   # ab hier gilt der Score auch ohne Margin
TH_ANCHOR_VETO = 0.12  # Null-Anker naeher als das am Sieger -> unsicher, ablehnen
                       # (0.10 verfehlte den Kalibrier-Fall um 0.005: workflow 0.547,
                       #  meta-none 0.442 auf Platz 3)

# Anker-Beschreibungen fürs multilinguale Embedding-Modell. Gemischt DE/EN,
# weil die User-Prompts gemischt sind. Bewusst unterscheidungsstark formuliert:
# jede Domain nennt ihr typisches Vokabular UND typische Aufgabenformen.
DOMAIN_DESCRIPTIONS = {
    "ui-frontend":
        "Benutzeroberflächen bauen und gestalten: Web-Frontend, HTML, CSS, Komponenten, "
        "Layout, Buttons, Styling, responsive Design, das Aussehen einer Seite oder App. "
        "Typical asks: build a UI, improve the interface, make it look modern, fix the layout.",
    "data-analysis":
        "Daten auswerten und visualisieren: CSV- oder JSON-Daten laden, aggregieren, "
        "Statistiken berechnen, Charts und Diagramme erzeugen, Tabellen und Zahlen analysieren. "
        "Typical asks: analyze this data, plot a chart, summarize the numbers, find trends.",
    "workflow":
        "Automatisierung und Agenten-Orchestrierung: Workflows, Pipelines, Cron-Jobs, Hooks, "
        "Subagenten und Multi-Agent-Loops einrichten, verketten oder verbessern. "
        "Typical asks: automate this process, orchestrate agents, schedule a recurring job.",
    "debug":
        "Fehler finden und beheben: Bugs, Crashes, Exceptions, Tracebacks, fehlschlagende "
        "Tests, unerwartetes oder kaputtes Verhalten reproduzieren und diagnostizieren. "
        "Typical asks: why does this fail, fix the error, the app crashes, tests are red.",
    "research":
        "Recherche und Wissensfragen: Informationen suchen, Quellen finden und vergleichen, "
        "Konzepte erklären lassen, Dokumentation oder Web durchsuchen, Optionen bewerten. "
        "Typical asks: find out how X works, what is Y, compare alternatives, gather sources.",
    "code-impl":
        "Code schreiben und umbauen: Funktionen, Klassen, Module oder Skripte implementieren, "
        "Refactoring, APIs entwickeln, Tests schreiben — konkrete Umsetzung statt Analyse. "
        "Typical asks: implement this function, write a script, refactor the module, add a feature.",
}

# Null-Anker gegen High-Score-False-Positives (Zero-Shot-Trick): diese Labels
# werden mitklassifiziert, sind aber keine Domains — gewinnt einer, lehnt
# pick_daemon_domain automatisch ab (Name nicht in DOMAIN_ROUTING) und der
# Keyword-Fallback übernimmt. Kalibrier-Befund 2026-07-02: die Meta-Frage
# "welche projekte liegen in meinem AI ordner" traf workflow mit 0.547 —
# über jedem sinnvollen Threshold, nur ein Null-Anker fängt so etwas.
NULL_ANCHORS = {
    "meta-none":
        "Meta-Fragen über den Workspace, Ordner und den Stand der Dinge: was liegt in welchem "
        "Ordner oder Verzeichnis, welche Projekte oder Dateien gibt es, Inventar und Überblick, "
        "was haben wir gemacht, weitermachen mit dem Plan, Status, Zusammenfassung, "
        "allgemeine Unterhaltung ohne fachliches Thema. "
        "Typical asks: what is in this folder, which projects exist, what did we do, continue.",
}


def _daemon_url():
    return os.environ.get("ATLAS_DAEMON_URL", ATLAS_DAEMON_URL_DEFAULT).rstrip("/")


def _daemon_timeout():
    try:
        return float(os.environ.get("ATLAS_DAEMON_TIMEOUT", ATLAS_DAEMON_TIMEOUT_DEFAULT))
    except Exception:
        return ATLAS_DAEMON_TIMEOUT_DEFAULT


def _http_post_json(url, body, timeout):
    """POST JSON, JSON zurück. Non-200 -> None. Wirft bei Netz-/Timeout-Fehlern
    (Caller fängt). Reines stdlib-urllib, kein requests."""
    req = urllib.request.Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if getattr(resp, "status", 200) != 200:
            return None
        return json.loads(resp.read().decode("utf-8"))


class DaemonBudget:
    """Gesamt-Zeitbudget für ALLE Daemon-Calls eines Hook-Laufs (~1.2s).
    Ist das Budget verbraucht, werden weitere Daemon-Calls übersprungen
    (Rückgabe None -> Caller fällt auf Keyword-/SQLite-Pfad zurück)."""

    def __init__(self, budget_s=DAEMON_BUDGET_S):
        self.budget_s = budget_s
        self.spent_s = 0.0

    def exhausted(self):
        return self.spent_s >= self.budget_s

    def call(self, fn, *args, **kwargs):
        if self.exhausted():
            return None
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self.spent_s += time.perf_counter() - t0


def classify_via_daemon(prompt, timeout=None, http_fn=None):
    """POST /classify mit den 6 Domain-Labels.
    -> [{"name","score"},...] absteigend, oder None bei JEDEM Fehler
    (Timeout, Connection-Refused, Non-200, kaputtes JSON, Schema-Drift)."""
    try:
        fn = http_fn or _http_post_json
        t = timeout if timeout is not None else _daemon_timeout()
        body = {
            "query": str(prompt)[:CLASSIFY_PROMPT_CAP],
            "labels": [{"name": n, "description": d}
                       for n, d in {**DOMAIN_DESCRIPTIONS, **NULL_ANCHORS}.items()],
        }
        data = fn(_daemon_url() + "/classify", body, t)
        scores = [{"name": str(s["name"]), "score": float(s["score"])} for s in data["scores"]]
        scores.sort(key=lambda s: -s["score"])
        return scores or None
    except Exception:
        return None


def pick_daemon_domain(scores):
    """Threshold-Politik (simpel, dokumentiert): akzeptiere das Top-Label wenn
    score >= TH_ACCEPT UND (Abstand zum Zweitplatzierten >= TH_MARGIN ODER
    score >= TH_CLEAR, d.h. absolut eindeutig). Unbekannte Label-Namen werden
    abgelehnt (Schutz gegen Daemon-Drift). Sonst None -> Keyword-Fallback."""
    try:
        if not scores:
            return None
        top = scores[0]
        if top["name"] not in DOMAIN_ROUTING or top["score"] < TH_ACCEPT:
            return None
        # Anker-Veto: liegt ein Null-Anker nahe am Sieger, ist der Prompt
        # meta-verdaechtig -> ablehnen (strenger als TH_MARGIN, Kalibrier-Befund:
        # "welche projekte liegen in meinem AI ordner" -> workflow 0.547 vs
        # meta-none 0.463 — nur dieses Veto faengt den Fall).
        for s in scores[1:]:
            if s["name"] in NULL_ANCHORS and s["score"] >= top["score"] - TH_ANCHOR_VETO:
                return None
        second = scores[1]["score"] if len(scores) > 1 else 0.0
        if top["score"] - second >= TH_MARGIN or top["score"] >= TH_CLEAR:
            return top["name"]
        return None
    except Exception:
        return None


def search_via_daemon(terms, k=3, timeout=None, http_fn=None):
    """POST /search. -> results-Liste (evtl. leer) oder None bei JEDEM Fehler."""
    try:
        fn = http_fn or _http_post_json
        t = timeout if timeout is not None else _daemon_timeout()
        data = fn(_daemon_url() + "/search", {"query": str(terms), "k": int(k)}, t)
        results = data["results"]
        return results if isinstance(results, list) else None
    except Exception:
        return None


def _cap_text(value):
    text = " ".join(str(value or "").split()).strip()
    # Live-Befund 2026-07-07: /search liefert teils Frontmatter-Delimiter
    # ("---") als Heading; solche Strukturzeichen sind kein nutzbarer Hint.
    if text and re.fullmatch(r"[-_#>\s]+", text):
        return ""
    return text


def format_cap_hint(result):
    """Kompakter Hint aus einem /search-Result: 'record_id — Titel' wenn heading/
    snippet vorhanden (informativer als nackte record_id), hart gekappt —
    Injektions-Budget! Fail-soft: kaputtes Result -> ''."""
    try:
        rid = str(result.get("record_id", "") or "").strip()
        title = _cap_text(result.get("heading")) or _cap_text(result.get("snippet"))
        if title and rid and title.lower() != rid.lower():
            return f"{rid} — {title[:60]}"
        return rid or title[:60]
    except Exception:
        return ""


# v7 Ghost-Mentor: "frühere gelöste Fälle" als zweite Partition DERSELBEN
# /search-Overfetch-Ergebnisse (kein zusätzlicher Daemon-Call, kein Budget-
# Impact). Allowlist statt "alles Nicht-atlas": wiki-Treffer existieren auch
# auf Junk-Queries (v5-Befund), darum zusätzlich ein Token-Overlap-Gate —
# ein Mentor-Hint muss min. 2 signifikante Query-Tokens tragen. Leer ist
# gewollt besser als falsch.
MENTOR_PREFIXES = ("haupt-wiki/queries/", "summary-harvest/", "agent-memory/")
MENTOR_LIMIT = 2
MENTOR_MIN_OVERLAP = 2


def _mentor_overlap_ok(text, terms):
    """Relevanz-Gate: min. MENTOR_MIN_OVERLAP signifikante Query-Tokens (>=4
    Zeichen) müssen im Kandidaten-Text vorkommen. RRF-Scores können das nicht
    leisten (v5-Befund) — Lexik-Overlap ist das billigste ehrliche Gate."""
    toks = {t.lower() for t in str(terms).split() if len(t) >= 4}
    if len(toks) < MENTOR_MIN_OVERLAP:
        return False
    low = str(text).lower()
    return sum(1 for t in toks if t in low) >= MENTOR_MIN_OVERLAP


def filter_mentor_results(results, terms, limit=MENTOR_LIMIT):
    """Mentor-Partition der Daemon-/search-Results: Präfix-Allowlist +
    Overlap-Gate, formatiert wie Caps-Hints. Fail-soft: Müll -> []."""
    try:
        if int(limit) <= 0:
            return []
        hints = []
        for r in results or []:
            rid = str((r or {}).get("record_id", "") or "")
            if not rid.startswith(MENTOR_PREFIXES):
                continue
            blob = " ".join([rid,
                             str((r or {}).get("heading", "") or ""),
                             str((r or {}).get("snippet", "") or "")])
            if not _mentor_overlap_ok(blob, terms):
                continue
            h = format_cap_hint(r)
            if h:
                hints.append(h)
            if len(hints) >= limit:
                break
        return hints
    except Exception:
        return []


def _query_mentor_sqlite(terms, db_path, limit=MENTOR_LIMIT):
    """SQLite/FTS5-Fallback der Mentor-Partition (nur record_ids, kein
    Heading). Overlap-Gate läuft über record_id + Chunk-Text."""
    if not str(terms).strip() or not db_path or limit <= 0:
        return []
    fts = build_fts_query(str(terms))
    if not fts:
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=0.5)
        try:
            like = " OR ".join("record_id LIKE ?" for _ in MENTOR_PREFIXES)
            rows = conn.execute(
                "SELECT record_id, text FROM chunks WHERE chunks MATCH ? AND (" + like + ") "
                "ORDER BY rank LIMIT 12",
                (fts, *[p + "%" for p in MENTOR_PREFIXES]),
            ).fetchall()
        finally:
            conn.close()
        hints = []
        for rid, text in rows:
            if _mentor_overlap_ok(f"{rid} {text}", terms):
                hints.append(rid)
            if len(hints) >= limit:
                break
        return hints
    except Exception:
        return []


def lookup_sources(terms, atlas_root, budget, http_fn=None, limit=3, daemon_ok=True):
    """Lookup-Kaskade für BEIDE Vorab-Suche-Partitionen: Daemon /search zuerst,
    bei Fehler/Budget-Skip Fallback auf den direkten SQLite/FTS5-Pfad.
    -> (caps, caps_source, caps_raw_count, mentors, mentor_source).
    daemon_ok=False (z.B. classify schlug schon fehl -> Daemon gilt für diesen
    Lauf als down) skippt den /search-Versuch komplett.

    Iteration 3/v5 (2026-07-07): /search-Scores sind RRF-Rank-Fusion
    (rrf_fuse, k_const=60) und clustern für gute wie Junk-Queries ähnlich bei
    ca. 0.014-0.023; Score-Thresholds taugen NICHT als Relevanz-Gate. Live-Probe
    mit k=10 zeigte: Capability-Treffer tragen record_id-Prefix "atlas/", Junk
    hatte 0 atlas/-Treffer. Darum ist der Prefix-Filter das Gate; leer nach dem
    Filter ist absichtlich besser als ein falscher Caps-Hint. Die Mentor-
    Partition (v7) nutzt dieselben Results mit eigener Allowlist + Overlap-Gate."""
    overfetch = max(12, int(limit))
    results = budget.call(search_via_daemon, terms, overfetch, http_fn=http_fn) if daemon_ok else None
    if results is not None:
        raw_count = len(results)
        filtered = [r for r in results if str((r or {}).get("record_id", "")).startswith("atlas/")]
        hints = [h for h in (format_cap_hint(r) for r in filtered[:limit]) if h]
        mentors = filter_mentor_results(results, terms)
        return (hints, ("daemon" if hints else "none"), raw_count,
                mentors, ("daemon" if mentors else "none"))
    db = find_atlas_db(atlas_root)
    caps, raw_count = _query_atlas_filtered(terms, db, limit=limit, raw_limit=overfetch)
    mentors = _query_mentor_sqlite(terms, db)
    return (caps, ("sqlite" if caps else "none"), raw_count,
            mentors, ("sqlite" if mentors else "none"))


def lookup_capabilities(terms, atlas_root, budget, http_fn=None, limit=3, daemon_ok=True):
    """Rückwärts-kompatibler Caps-Blick auf lookup_sources (3-Tupel-Kontrakt)."""
    return lookup_sources(terms, atlas_root, budget, http_fn=http_fn,
                          limit=limit, daemon_ok=daemon_ok)[:3]


def run(payload, *, atlas_root, state_dir, log_path, now, http_fn=None, budget=None, decision_log_path=None):
    if not isinstance(payload, dict):
        return ""
    prompt = payload.get("prompt", "") or ""
    session_id = payload.get("session_id", "default") or "default"
    preview = str(prompt).strip()[:80]
    decision_log_path = decision_log_path or _default_decision_log_path()
    work_signals = detect_work_signals(prompt)

    skip, reason = should_skip(prompt)
    if skip:
        log_telemetry({"t": now, "skip": reason, "session": session_id,
                       "prompt_preview": preview}, log_path)
        log_decision(decision_record("skip", reason, now=now, session_id=session_id,
                                     prompt_preview=preview, work_signals=work_signals),
                     decision_log_path)
        return ""

    budget = budget or DaemonBudget()

    # Routing-Kaskade: (a) Daemon-Klassifikation, (b) Keyword-Fallback.
    # Beide Ergebnisse werden IMMER geloggt (A/B-Vergleich zur Kalibrierung).
    spent_before = budget.spent_s
    daemon_scores = budget.call(classify_via_daemon, prompt, http_fn=http_fn)
    daemon_latency_ms = round((budget.spent_s - spent_before) * 1000, 1)
    daemon_domain = pick_daemon_domain(daemon_scores)
    keyword_domain, dom_hits = match_domain(prompt)

    if daemon_domain:
        domain, routing_source = daemon_domain, "daemon"
    elif keyword_domain:
        domain, routing_source = keyword_domain, "keywords"
    else:
        # v3: kein Spezial-Routing -> general-Fallback (statt no_routing/Stille).
        # Jeder substantielle Prompt zieht so die RAG-Vorabsuche + Caps-Injektion.
        domain, routing_source = "general", "fallback"

    ab = {  # A/B-Telemetrie: hängt an JEDEM post-classify Event
        "routing_source": routing_source,
        "daemon_top": [{"name": s["name"], "score": round(s["score"], 3)}
                       for s in (daemon_scores or [])[:3]],
        "keyword_domain": keyword_domain,
        "daemon_latency_ms": daemon_latency_ms,
    }

    # Phase-Erkennung bleibt bewusst Keyword-basiert (nicht Daemon).
    phase, phase_hits = match_phase(prompt)
    routing = build_rag_routing(domain, phase)
    confidence = domain_confidence(domain, routing_source, daemon_scores, dom_hits, work_signals)
    classification = {
        "domain": domain,
        "phase": phase,
        "routing_source": routing_source,
        "confidence": round(confidence, 3),
        "threshold": PRECISION_CONFIDENCE_THRESHOLD,
        "daemon_top": ab["daemon_top"],
        "keyword_domain": keyword_domain,
        "matched_keywords": dom_hits + phase_hits,
    }

    if not work_signals:
        log_telemetry({"t": now, "skip": "no_work_signal", "session": session_id,
                       "prompt_preview": preview, **ab}, log_path)
        log_decision(decision_record("skip", "no_work_signal", now=now,
                                     session_id=session_id, prompt_preview=preview,
                                     classification=classification,
                                     work_signals=work_signals), decision_log_path)
        return make_skip_status("no_work_signal", domain, phase)

    if confidence < PRECISION_CONFIDENCE_THRESHOLD:
        log_telemetry({"t": now, "skip": "low_domain_confidence", "session": session_id,
                       "prompt_preview": preview, "confidence": round(confidence, 3), **ab}, log_path)
        log_decision(decision_record("skip", "low_domain_confidence", now=now,
                                     session_id=session_id, prompt_preview=preview,
                                     classification=classification,
                                     work_signals=work_signals), decision_log_path)
        return make_skip_status("low_domain_confidence", domain, phase)


    if not routing:
        log_telemetry({"t": now, "skip": "no_routing", "session": session_id,
                       "prompt_preview": preview, **ab}, log_path)
        log_decision(decision_record("skip", "no_routing", now=now, session_id=session_id,
                                     prompt_preview=preview, classification=classification,
                                     work_signals=work_signals), decision_log_path)
        return make_skip_status("no_routing", domain, phase)

    cleanup_state(state_dir, now)

    key = dedupe_key(domain, phase, topic_signature(prompt))
    fired = load_fired(session_id, state_dir)
    rearmed = False
    if key in fired:
        if has_rag_reference(prompt):
            rearmed = True  # expliziter RAG-Bezug schlägt Dedupe
        else:
            log_telemetry({"t": now, "skip": "deduped", "key": key, "session": session_id,
                           "prompt_preview": preview, **ab}, log_path)
            log_decision(decision_record("skip", "deduped", now=now, session_id=session_id,
                                         prompt_preview=preview, classification=classification,
                                         work_signals=work_signals), decision_log_path)
            return make_skip_status("deduped", domain, phase)

    terms = extract_query(prompt)
    caps, caps_source, caps_raw_count, mentors, mentor_source = lookup_sources(
        terms, atlas_root, budget, http_fn=http_fn,
        daemon_ok=daemon_scores is not None)
    # Unter 2 Content-Tokens ist die Vertiefungszeile Rauschen
    # (Live-Smoke 2026-07-07: memory_search_tool("weiter") auf Junk-Prompt).
    ctx = compose_context(domain, phase, routing, caps,
                          query=terms if len(terms.split()) >= 2 else None,
                          mentors=mentors)
    # systemMessage nur beim tatsächlichen Feuern (Q1: sichtbare Zeile nur wenn feuert).
    sys_msg = build_system_message(domain, phase, caps, caps_source, mentors) if ctx else None
    out = make_output(ctx, system_message=sys_msg)

    if out:
        fired.add(key)
        save_fired(session_id, state_dir, fired)
        log_decision(decision_record("emit", "precision_gate_pass", now=now,
                                     session_id=session_id, prompt_preview=preview,
                                     classification=classification,
                                     work_signals=work_signals), decision_log_path)
        log_telemetry({"t": now, "fired": True, "domain": domain, "phase": phase,
                       "key": key, "caps": caps, "caps_count": len(caps),
                       "caps_raw_count": caps_raw_count,
                       "caps_source": caps_source, "query": terms,
                       "mentor": mentors, "mentor_count": len(mentors),
                       "mentor_source": mentor_source,
                       "rearmed": rearmed, "prompt_preview": preview,
                       "matched_keywords": dom_hits + phase_hits,
                       "session": session_id, **ab}, log_path)
    return out


def _default_state_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe")


def _default_log_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_prelude.jsonl")


def _default_decision_log_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prelude_decisions.jsonl")


def _read_stdin_utf8(max_bytes=200_000):
    """stdin über den BYTE-Buffer lesen und explizit als UTF-8 dekodieren.

    Live-Bug 2026-07-06: der Text-Stream sys.stdin dekodiert Pipe-stdin auf
    Windows als cp1252 -> jeder Umlaut wurde Mojibake ("möchte" -> "mÃ¶chte",
    0/208 v3-Events mit korrekten Umlauten). Folgeschäden: Umlaut-Keywords
    matchten nie, die Daemon-Klassifikation lief auf Müll-Text (1/123
    daemon-Routings) und extract_query produzierte Fragmente ("chte").
    _force_utf8 hilft hier nicht zuverlässig (stdin-reconfigure greift nicht
    auf allen Stream-Typen) — Bytes lesen + selbst dekodieren ist der einzig
    robuste Pfad. Fail-soft: exotische stdins ohne .buffer -> Text-Stream."""
    try:
        return sys.stdin.buffer.read(max_bytes).decode("utf-8", "replace")
    except Exception:
        try:
            return sys.stdin.read(max_bytes)
        except Exception:
            return ""


def main():
    try:
        log_path = _default_log_path()
        raw = _read_stdin_utf8()  # bounded gegen riesigen Paste, explizit UTF-8
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            # abgeschnittenes/invalides stdin-JSON: Skip-Event loggen, nie blockieren
            log_telemetry({"t": time.time(), "skip": "bad_stdin",
                           "prompt_preview": (raw or "")[:80]}, log_path)
            return 0
        try:
            out = run(
                payload,
                atlas_root=ATLAS_ROOT_DEFAULT,
                state_dir=_default_state_dir(),
                log_path=log_path,
                now=time.time(),
            )
            if out:
                print(out)
        except Exception as e:
            # best-effort Crash-Telemetrie; log_telemetry ist selbst fail-soft
            prompt = payload.get("prompt") if isinstance(payload, dict) else None
            log_telemetry({"t": time.time(), "skip": "crash", "error": str(e)[:200],
                           "prompt_preview": str(prompt or "")[:80]}, log_path)
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
