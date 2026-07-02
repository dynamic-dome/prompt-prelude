#!/usr/bin/env python3
"""UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG.
stdlib-only. Fail-soft: Fehler -> kein Output. Exit immer 0.
Opt-out: Prompt mit //raw prefixen."""
import sys, os, json, re, time
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


# Keyword-Konvention: Wortgrenzen-Match (\b...\b). Ein trailing "*" macht das
# Keyword zum Präfix-Stem (\bstem...), z.B. "implementier*" matcht "implementieren".
# Substring-Matching ist verboten (Live-Befund: "ui" matchte "build"/"guide"/"quiet"
# -> ui-frontend 34/51 Domain-Events überrepräsentiert).
DOMAIN_HINTS = {
    "ui-frontend":   ["ui", "css", "component*", "layout*", "responsive", "frontend*",
                      "button*", "styling", "oberfläche", "oberflaeche", "interface*"],
    "data-analysis": ["daten", "auswert*", "csv", "analyse", "chart*", "viz", "dashboard*", "tabelle*"],
    "workflow":      ["workflow*", "loop*", "orchestrier*", "subagent*", "pipeline*", "cron*", "agenten"],
    "debug":         ["bug*", "fehler*", "crash*", "traceback*", "kaputt*", "debug*", "exception*"],
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
    """Baut den <prompt_prelude>-Block. Leer-String, wenn nichts Relevantes.

    Die ECHO-Zeile (erzwungene erste Antwortzeile) war Rollout-Verifikation und
    ist nur noch mit Env PRELUDE_ECHO=1 aktiv (Default: aus)."""
    if not routing_lines and not capabilities:
        return ""
    dom = domain or "-"
    parts = []
    if os.environ.get("PRELUDE_ECHO") == "1":
        parts.append(f"ECHO: Beginne deine Antwort mit genau einer Zeile: "
                     f"↳ prelude · [{phase}] [{dom}] · RAG-Auftrag aktiv")
        parts.append("")
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
    """Feiner Key domain:phase — quiet->planning derselben Domain feuert erneut
    (Live-Befund 1: reiner Domain-Key war zu grob, 35 deduped vs. 16 fired)."""
    if domain:
        return f"{domain}:{phase}"
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
    domains = [d for d, kws in DOMAIN_HINTS.items()
               if any(_kw_regex(kw).search(low) for kw in kws)]
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


def build_fts_query(terms):
    """FTS5-sichere Query: Bindestriche zu getrennten Termen, jeder Term
    doppelt-gequotet, OR-verknüpft. Ein rohes 'ui-frontend' warf vorher
    OperationalError 'no such column: frontend' -> caps war in 16/16 Live-Fires
    leer. ANDed Tokens matchen bei bis zu 12 Termen praktisch nie -> OR ist Pflicht."""
    tokens = [t for t in re.split(r"[\s\-]+", terms) if t]
    return " OR ".join('"{}"'.format(t.replace('"', '""')) for t in tokens)


def query_atlas(terms, db_path, limit=3):
    if not terms.strip() or not db_path:
        return []
    fts = build_fts_query(terms)
    if not fts:
        return []
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=0.5)
        try:
            rows = conn.execute(
                "SELECT record_id FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT ?",
                (fts, limit),
            ).fetchall()
        finally:
            conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


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
TH_ACCEPT = 0.45  # Kalibrierungs-Kandidat: Mindest-Score des Bestplatzierten
TH_MARGIN = 0.05  # Kalibrierungs-Kandidat: Mindestabstand zum Zweitplatzierten
TH_CLEAR = 0.50   # Kalibrierungs-Kandidat: ab hier gilt der Score auch ohne Margin
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


def format_cap_hint(result):
    """Kompakter Hint aus einem /search-Result: 'record_id — Titel' wenn heading/
    snippet vorhanden (informativer als nackte record_id), hart gekappt —
    Injektions-Budget! Fail-soft: kaputtes Result -> ''."""
    try:
        rid = str(result.get("record_id", "") or "").strip()
        title = " ".join(str(result.get("heading") or result.get("snippet") or "").split()).strip()
        if title and rid and title.lower() != rid.lower():
            return f"{rid} — {title[:60]}"
        return rid or title[:60]
    except Exception:
        return ""


def lookup_capabilities(terms, atlas_root, budget, http_fn=None, limit=3, daemon_ok=True):
    """Caps-Lookup-Kaskade: Daemon /search zuerst, bei Fehler/Budget-Skip
    Fallback auf den direkten SQLite/FTS5-Pfad. -> (hints, caps_source).
    daemon_ok=False (z.B. classify schlug schon fehl -> Daemon gilt für diesen
    Lauf als down) skippt den /search-Versuch komplett: Windows brennt für
    connection-refused auf localhost den VOLLEN Timeout ab (~0.5s gemessen),
    ein zweiter toter Call wäre reine Latenz-Verschwendung."""
    results = budget.call(search_via_daemon, terms, limit, http_fn=http_fn) if daemon_ok else None
    if results is not None:
        hints = [h for h in (format_cap_hint(r) for r in results[:limit]) if h]
        return hints, ("daemon" if hints else "none")
    caps = query_atlas(terms, find_atlas_db(atlas_root), limit)
    return caps, ("sqlite" if caps else "none")


def run(payload, *, atlas_root, state_dir, log_path, now, http_fn=None, budget=None):
    if not isinstance(payload, dict):
        return ""
    prompt = payload.get("prompt", "") or ""
    session_id = payload.get("session_id", "default") or "default"
    preview = str(prompt).strip()[:80]

    skip, reason = should_skip(prompt)
    if skip:
        log_telemetry({"t": now, "skip": reason, "session": session_id,
                       "prompt_preview": preview}, log_path)
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
        domain, routing_source = None, "none"

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

    if not routing:
        log_telemetry({"t": now, "skip": "no_routing", "session": session_id,
                       "prompt_preview": preview, **ab}, log_path)
        return ""

    cleanup_state(state_dir, now)

    key = dedupe_key(domain, phase)
    fired = load_fired(session_id, state_dir)
    rearmed = False
    if key in fired:
        if has_rag_reference(prompt):
            rearmed = True  # expliziter RAG-Bezug schlägt Dedupe
        else:
            log_telemetry({"t": now, "skip": "deduped", "key": key, "session": session_id,
                           "prompt_preview": preview, **ab}, log_path)
            return ""

    caps, caps_source = lookup_capabilities(extract_query(prompt), atlas_root,
                                            budget, http_fn=http_fn,
                                            daemon_ok=daemon_scores is not None)
    ctx = compose_context(domain, phase, routing, caps)
    out = make_output(ctx)

    if out:
        fired.add(key)
        save_fired(session_id, state_dir, fired)
        log_telemetry({"t": now, "fired": True, "domain": domain, "phase": phase,
                       "key": key, "caps": caps, "caps_count": len(caps),
                       "caps_source": caps_source,
                       "rearmed": rearmed, "prompt_preview": preview,
                       "matched_keywords": dom_hits + phase_hits,
                       "session": session_id, **ab}, log_path)
    return out


def _default_state_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe")


def _default_log_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompt_prelude.jsonl")


def main():
    try:
        log_path = _default_log_path()
        try:
            raw = sys.stdin.read(200_000)  # bounded gegen riesigen Paste
        except Exception:
            raw = ""
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
