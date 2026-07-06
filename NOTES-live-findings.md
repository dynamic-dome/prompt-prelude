# prompt-prelude — Live-Befunde (erste reale Session 2026-06-30)

Aus echter Telemetrie (`prompt_prelude.jsonl`), nicht spekuliert.

## Befund 1: Dedupe unterdrückt wiederholt-relevante Aufträge
Session `28eca95c…`: erster UI-Prompt `fired ui-frontend`, ein späterer Prompt, der
EXPLIZIT erneut den UI-RAG-Auftrag wollte, wurde `skip: deduped`. Der Cry-Wolf-Trade-off
ist real, aber die harte "einmal pro Session"-Regel ist zu grob.

**Verbesserungs-Idee:** Dedupe-Key feiner — statt nur `domain` ein `domain+phase`-Key
(quiet→planning bei derselben Domain feuert dann erneut), ODER ein Re-Arm nach N Prompts,
ODER Re-Arm wenn der Prompt einen expliziten RAG-/Skill-Bezug enthält ("welche skills",
"ins rag schauen", "semantische suche").

## Befund 2: Planning-Trigger zu eng
Eigene Session blieb `quiet`, obwohl klar Planungs-/Machbarkeitsphase. Fehlende Trigger:
`durchspielen`, `klären`, `machbar`, `durchführbar`, `feasibility`, `grundgerüst`, `hülle`.
Eine Parallel-Session erkannte `planning` korrekt → Mechanik ok, nur Vokabular zu schmal.

**Verbesserungs-Idee:** PLANNING_TRIGGERS um obige Wörter erweitern.

## Befund 3: AskUserQuestion-Antworten umgehen den Hook (by design)
`UserPromptSubmit` feuert nur bei freien Text-Prompts, nicht bei Multiple-Choice-Antworten.
Das ist korrekt und nicht zu "fixen" — aber dokumentieren: Routing-Aufträge in reinen
Frage-Antwort-Sequenzen erscheinen nicht. Kein Bug, eine Grenze.

## Befund 4 (2026-07-02, Session Hook-Analyse): Harness-Prompts verzerren alles
`<task-notification>`-Prompts (Subagent-Callbacks) wurden geroutet wie User-Prompts —
Fehl-Routings (ui-frontend auf Telemetrie-Reports) und verzerrte H4-Compliance.
**Umgesetzt:** `machine_prompt`-Skip für `<task-notification>`, `<system-reminder>`,
`<local-command-stdout>`, `<command-name>` am Prompt-Anfang.

## Befund 5 (2026-07-06, KRITISCH): stdin wurde als cp1252 gelesen — alle Umlaute Mojibake
`main()` las stdin über den Text-Stream; `_force_utf8` deckte nur stdout/stderr ab.
Python dekodiert Pipe-stdin auf Windows als cp1252 → "möchte" kam als "mÃ¶chte" an.
Telemetrie-Beweis: **0/208 v3-Events mit korrekten Umlauten**, 58 mit Mojibake-Artefakten.
Kaskadenschäden: Umlaut-Keywords (`klär*`, `fähigkeiten`, `oberfläche`, …) matchten live NIE,
Daemon-Klassifikation lief auf Müll-Text (nur **1/123** fired-Events via daemon geroutet,
obwohl die Eval 18/20 zeigte), `extract_query` produzierte Fragmente ("chte", "hinzuf").

**Meta-Lektion:** `eval_routing.py` füttert Prompts in-process (sauberes UTF-8) und konnte
den Bug prinzipiell nicht finden — die Eval-vs-Live-Diskrepanz (18/20 vs 1/123) WAR das
Signal. Entspricht der Lese-Seite von globaler CLAUDE.md §10 Regel 11 (cp1252-stdin).
**Umgesetzt (Iteration 3, v4):** `_read_stdin_utf8()` liest `sys.stdin.buffer` (Bytes) und
dekodiert explizit UTF-8; E2E-Test mit echtem Subprocess-stdin (`TestStdinEncodingE2E`),
den In-Process-Tests nicht ersetzen können. Telemetrie v4 — v1-v3-Daten sind für
Routing-/Compliance-Auswertungen Mojibake-vergiftet, NICHT mit v4 mischen.
**Offen:** Threshold-Kalibrierung + eval_compliance nach ~1 Woche v4-Daten neu fahren.

## Befund 6 (2026-07-07): general-Fallback injizierte ungefiltertes Rauschen
Der v3-Breitband-Fallback nahm blind die Top-3 von `/search` — bei Smalltalk-/
Junk-Queries irrelevante Wiki-Notizen (Live: Gammateleskop-Vorlesung + Flughafen-
Roboter auf einen Projekt-Prompt, gematcht übers Mojibake-Fragment "Flug").
Drei Ursachen: kein Relevanz-Gate, Index-Scope = ganzer Atlas statt Capabilities,
Domain-Labels verschmutzten die Query. Score-Gate unmöglich: `/search`-Scores
sind RRF-Rang-Fusion (~0.014–0.023, gut wie Müll identisch — gemessen).
**Umgesetzt (v5, via Codex-Builder + Claude-Review):** atlas/-Präfix-Filter mit
k=12-Überholung (Probe: gute Queries 1–2 atlas/-Treffer in Top-10, Junk 0 —
der Präfix-Filter IST das Gate), "---"-Heading-Fallback, Query ohne
Label-Präfix + erweiterte Stopwörter, `caps_raw_count`-Telemetrie,
Vertiefungszeile erst ab 2 Content-Tokens. Leere Caps sind gewollt besser
als falsche. Codex-Scope-Creep (Sandbox-Hacks in conftest, Überfiltern von
Content-Wörtern wie "frontend") im Review zurückgebaut.

## Status (aktualisiert 2026-07-02 abend, Iteration 1)
- Befund 1: `domain+phase`-Key + RAG-Bezug-Re-Arm umgesetzt (frühere Session).
  **Offen:** Re-Arm nach N Prompts (State-Format `set` → `{key: fired_at}`) —
  als TODO im Feature-Quartett-Backlog persistiert.
- Befund 2: PLANNING_TRIGGERS erweitert (frühere Session). Zusätzlich Iteration 1:
  Agent-Tooling-Vokabular (`hook*`, `skill*`, `mcp*`) → workflow.
- Befund 3: dokumentiert, by design.
- Befund 4: umgesetzt (siehe oben).
- H1-Wording-Iteration umgesetzt: Selbst-Entwertung ("kein Befehl"/"optional")
  entfernt, Caps als vorgezogenes Suchergebnis, fertige memory_search_tool-Query
  im Kontext. Telemetrie-Schema v2 (`"v": 2`) — H4-Vergleich nur innerhalb v2 fahren.
