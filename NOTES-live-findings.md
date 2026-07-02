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
