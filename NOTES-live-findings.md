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

## Befund 7 (2026-07-21, T-23): v4+-Re-Messung — UTF-8-Fix vervierfachte die Compliance, aber die Metrik ist stumpf
Re-Messung nach dem stdin-UTF-8-Fix (Befund 5), v1-v3 sauber ausgeschlossen.
`eval_compliance.py` bekam dafür einen `--min-version`-Filter (Default v>=4;
`--min-version 0` = alte Misch-Auswertung). Datenstand: v4-v7, 2051 Zeilen, 372 fired.

**Zahlen (Fenster 900 s):**
- **FIRED befolgt: 67/372 = 18 %** (Median-Latenz 51 s) — vs. H4-Altbefund **~4 %**
  (1/26) auf Mojibake-v3. Der UTF-8-Fix hat die *gemessene* Compliance ~vervierfacht.
- Gegenprobe mit allen Versionen: 85/537 = 16 % (Mojibake zieht runter).
- Nach routing_source: keywords 21 %, fallback 16 %, **daemon nur 13 % (n=15)** —
  Daemon-Routing wird real kaum noch gezogen.

**Kernbefund (bestätigt die H4-These mit Zahlen):** Die SKIP-Baseline (Prelude feuert
*keine* frische Zeile) liegt bei **15 %** (256/1679). Fired hebt den Atlas-Call also nur
von 15 % → 18 % = **+3 pp**. Die Metrik kann "Hook nutzlos" nicht von "Hook so nützlich,
dass der Agent gar nicht mehr selbst sucht" trennen — genau weil die Prelude die Caps
*fertig injiziert*. `eval_compliance` misst Unterlassung dort, wo teils schon konsumiert wurde.

**Teil (b) — Zitat-Metrik — GEBLOCKT (Owner-Entscheid nötig):** Die bessere Metrik wäre
"zitiert der Folgeturn einen injizierten `caps`-Treffer (record_id/abs_path)?". Nicht
messbar mit der jetzigen Instrumentierung: der **tool-usage-tracker loggt keine
Tool-Argumente** — `memory_cite`-Events haben leeres `summary` (kein record_id), nur
`Read` trägt einen Pfad-Fragment im `summary`. Damit ist nur ein Teil-Proxy machbar
(Read auf einen injizierten `.md`-Pfad wie `atlas/approaches/internal/*.md`), der
record_id-Zitat-Zweig (`atlas/project:X` via memory_cite) bleibt blind. Voll-Metrik
braucht eine Tracker-Erweiterung (Argument-Logging: record_id + file_path) — cross-project,
mit Privacy-Implikation. **Bewusst NICHT still halbgebaut.** Owner-Optionen im Wrap-up.

## Befund 8 (2026-07-22): der Hook bewarb zwei Skills, die es nicht mehr gibt
Beim Bau des Skill-Routings aufgefallen, nicht gesucht: `DOMAIN_ROUTING["debug"]`
empfahl **`diagnose-hitl`** — der liegt in `~/.claude/skills/_archive/`.
`DOMAIN_ROUTING["ui-frontend"]` empfahl **`modern-web-design`**, dessen Plugin in
`enabledPlugins` auf `false` steht. Beide Zeilen liefen monatelang live und
schickten den Agenten auf Skills, die er nicht laden kann.

**Ursache:** Routing-Strings und Skill-Bestand haben keine Kopplung — eine
Aufräumrunde im `~/.claude/skills/`-Ordner merkt nichts vom Hook.
**Umgesetzt:** beide Zeilen korrigiert (→ `superpowers:systematic-debugging`
bzw. `web-design-guidelines`) + Regressions-Guard `TestNoDeadSkillReferences`
(hermetisch: feste Liste bekannter Leichen, kein Dateisystem-Zugriff, damit der
Test nicht vom Rechner abhängt).
**Offen:** Der Guard kennt nur, was jemand einträgt. Ein Live-Abgleich gegen
`~/.claude/skills/` + `enabledPlugins` wäre schärfer, wäre aber nicht hermetisch
— bewusst nicht gebaut.

## Befund 9 (2026-07-22): Skill-Routing als zweiter, härterer Kanal
Motivation direkt aus Befund 7: der RAG-Kanal liefert Caps fertig mit, deshalb
kann `eval_compliance` "ignoriert" nicht von "schon geliefert" trennen und misst
nur +3pp. Ein SKILL.md-Body lässt sich **nicht** vorab injizieren — der Agent
ruft ihn auf oder nicht. Damit ist der Join sauber und die Architekturfrage aus
T-4 (advisory vs. PreToolUse-Gate) erstmals empirisch entscheidbar.

**Umgesetzt (v8):** `SKILL_RULES` / `SKILL_ROUTING` / `SKILL_PHASE_ROUTING`,
imperativ formuliert mit `Skill("name")` und explizitem NICHT samt Begründung;
Deckel bei 2 Zeilen; eigener Block im Prelude **vor** dem RAG-Auftrag (die
Aktion vor dem Hintergrundmaterial); Telemetrie `skill_hint`/`skill_hint_count`;
`eval_skill_routing.py`.

**Baseline vor Einführung (v4+, Fenster 900 s): 37/389 = 10 %** — so oft wurde
einer der routbaren Skills ohne jeden Hinweis von selbst gerufen. Das ist die
Latte; ein FOLLOW um 10 % widerlegt den advisory-Kanal endgültig.

**Messfehler-Warnung, teuer gelernt:** Die erste Nutzungsanalyse zählte nur
`"skill":"…"` (Skill-Tool) und übersah `<command-name>/x</command-name>` — vom
Menschen getippte Slash-Commands. Dadurch erschienen real benutzte Skills als
"0 Aufrufe" (`agentic-os:session-bootstrap`: 9 statt 230). `eval_skill_routing`
zählt beide Quellen. Wer eine Skill-Statistik baut: **beide Kanäle, immer.**

**Offener Vorbehalt (nicht gelöst):** Der `planning`-Zweig hängt hinter dem
bestehenden `work_signal`-Gate. Ein reiner Überlegungs-Prompt ("ich überlege ein
konzept für die neue oberfläche…") wird als `no_work_signal` geskippt — genau
der Prompt-Typ, für den `office-hours`/`brainstorming` gedacht sind. Das
Phasen-Routing dürfte deshalb selten feuern. Erst messen (`skill_hint`-Rate für
die Planungszeile), dann entscheiden, ob das Gate für diesen Zweig aufgeweicht
wird — nicht vorab am Gate drehen.

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
