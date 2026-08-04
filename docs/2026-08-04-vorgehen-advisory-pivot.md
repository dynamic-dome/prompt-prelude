# Vorgehen: Advisory-Pivot (nach der v8-Inventur 2026-08-04)

Datenbasis: NOTES Befund 10 (Skill-Routing 11 % Follow vs. 6 % Baseline —
vorregistrierte Falsifikationsschwelle gerissen) und Befund 11 (Trajektor
beerdigt). Leitfrage des Owners: Was wollen wir überhaupt übermitteln, und
wie setzen wir das safe um?

## Leitidee: Mechanismus folgt Intention

Der Hook vermischt bisher drei Intentionen in einem Kanal. Die Daten zeigen,
dass jede Intention einen eigenen Mechanismus braucht:

| Intention | Richtiger Mechanismus | Beleg |
|---|---|---|
| **Material liefern** (Caps, Mentoren, frühere Fälle) | Passive Injektion zum Prompt-Zeitpunkt | Einzige plausible Quelle der +3–5 pp; kostet den Agenten nichts |
| **Verhalten empfehlen** (Skill X jetzt nutzen) | Hinweis am **Handlungsmoment**, nicht am Prompt | 0 % Follow bei allen Skills, deren Moment später liegt; 12 % bei den beiden Prompt-Zeitpunkt-Skills |
| **Regeln erzwingen** (Test-DB-Isolation) | PreToolUse-Gate, deterministisch | Advisory ≈ 8–13 % Compliance; ein Gate ist 100 % |

Safe-Prinzip dazu: **kein Material → kein Feuer.** Ein generischer Auftragstext
ohne konkrete Treffer ist Rauschen (52 % der v8-Fired hatten `caps_count=0`)
und verbraucht Cry-Wolf-Budget.

## Phase 0 — Housekeeping (erledigt 2026-08-04)

- [x] Trajektor offiziell archiviert (README + Befund 11).
- [x] `prelude_decisions.jsonl` im README dokumentiert.
- [x] `t=0`-Timestamp-Bug: Ursache war Test-Pollution (Tests ohne
  `decision_log_path`-Override fielen auf das ECHTE Log zurück) — behoben
  durch autouse-Fixture `hermetic_default_logs` in `conftest.py`.

## Phase 1 — Prelude v9: Eindampfen auf den Material-Kanal — UMGESETZT 2026-08-04

Kleinster Eingriff mit dem größten Rausch-Gewinn. Änderungen (alle live,
276 Tests grün, E2E-Smoke verifiziert):

1. **Feuer-Bedingung:** nur noch emittieren, wenn `caps_count > 0` ODER
   `mentor_count > 0`. Sonst neuer Skip-Grund `no_material` (Telemetrie!).
   Der `general`-Fallback ohne Treffer entfällt damit automatisch.
2. **RAG-Auftragstext:** auf eine Zeile reduziert und nur als Rahmen um echte
   Treffer ("Vorab-Treffer unten prüfen, Vertiefung: memory_search_tool(…)").
   Die domänenspezifischen Imperativ-Absätze entfallen.
3. **Skill-Routing im Prompt-Kanal:** nur die zwei Zeilen behalten, deren
   Handlungsmoment der Prompt-Zeitpunkt ist — `subagent-briefing` (12 %) und
   `superpowers:systematic-debugging` (12 %). `review`, `verify-subagent-tallies`,
   `office-hours`, `plan-ceo-review`, `sqlite-schema-guard` fliegen hier RAUS
   (sie ziehen in Phase 2 an ihre richtigen Lifecycle-Punkte um).
4. **Precision-Gate:** `phase == planning` zählt als eigenes work_signal
   (Befund 9/10: 59 fälschlich geskippte Planungs-Prompts). Erwartung: mehr
   brainstorming-taugliche Prompts erreichen den Materialkanal.
5. Telemetrie-Schema **v9**; Auswertungen nie mit v8 mischen.

**Messlatte (vorregistriert):** Nach 2 Wochen v9:
(a) Skip-Verteilung — `no_material`-Anteil zeigt, wie oft der Kanal vorher
leer gefeuert hat; (b) `eval_skill_routing` nur für die 2 verbliebenen Skills —
bleiben sie ≥ 2× Baseline, bleiben sie; (c) `eval_compliance` als Trend, ohne
Entscheidungsgewicht (Metrik bleibt stumpf, Befund 7).

Aufwand: ~2–4 h inkl. Tests (bestehende Testbasis ist stark).

## Phase 2 — Empfehlungen an den Handlungsmoment verschieben

Pro Regel ein kleiner, eigener Hook-Eintrag — nicht ein neuer Monolith.
Reihenfolge nach erwartetem Nutzen:

1. **`verify-subagent-tallies` → PostToolUse auf Task/Agent-Ergebnisse.**
   Trigger: Tool-Result eines Subagenten enthält Zahlen-Muster
   (Tallies, "N/M", Prozentangaben, DONE/OPEN-Summen). Aktion:
   additionalContext-Einzeiler mit fertigem Skill-Aufruf. Das ist die
   0/50-Zeile aus Befund 10 — der Hinweis kam bisher Minuten zu früh.
2. **`sqlite-schema-guard` → PreToolUse auf Bash `pytest`.**
   Trigger: Kommando matcht `pytest` und Projekt enthält sqlite-Nutzung.
   Stufe 1 advisory (additionalContext), Stufe 2 siehe Phase 3.
3. **`review`/End-of-Task-Skills → Stop-Hook, bewusst zurückgestellt.**
   Der Stop-Moment ist häufig und das Cry-Wolf-Risiko dort am größten;
   außerdem existiert das Codex-Stop-Review-Gate bereits als Kanal.
   Erst evaluieren, wenn 1.+2. gemessen sind.

**Messlatte (vorregistriert), pro Hook einzeln:** eigene Telemetrie-Datei,
Follow-Join analog `eval_skill_routing` (Skill-Call ODER Slash-Command im
15-Min-Fenster nach Hint). Falsifikation: Follow < 2× der jeweiligen Baseline
nach 2 Wochen → Hook wieder raus. Nicht verhandelbar: jeder neue Hook startet
mit seinem Abschalt-Kriterium.

Aufwand: ~1 Tag für 1.+2. inkl. Telemetrie.

## Phase 3 — T-4 aufmachen: Enforcement für die eine harte Regel

Test-DB-Isolation ist die einzige Regel mit "NIEMALS"-Status in der globalen
CLAUDE.md. Advisory hat dafür nachweislich die falsche Compliance-Klasse.

- PreToolUse-**Gate** (deny oder ask) auf `pytest`-Kommandos, wenn der
  statische Isolation-Check (sqlite-schema-guard-Logik: lazy `_db_path()`,
  conftest-Override) fehlschlägt.
- Integrationsort: der bestehende `block_destructive.py`-Hook
  (pretooluse-block-hook-test) statt eines weiteren Prozess-Spawns —
  das UserPromptSubmit-Budget (2 s) bleibt unberührt.
- Scope bewusst minimal: NUR diese Regel wird enforcing. Alles andere bleibt
  advisory oder Material. Ein breites Gate-Regime wäre der gegenteilige
  Fehler zum jetzigen breiten Advisory-Regime.

Aufwand: ~0,5–1 Tag; der Skill enthält die Prüflogik schon.

## Weitblick (über die 3 Phasen hinaus)

- **Der eigentliche Asset dieses Projekts ist die Mess-Methodik**, nicht der
  Hook: Telemetrie-Ären, vorregistrierte Falsifikationsschwellen, Join gegen
  Transkripte/Tracker. Diese Methodik gehört auf jeden künftigen Hook
  übertragen (Template: Telemetrie + Eval + Abschalt-Kriterium ab Tag 1).
- **Konsolidierung der Kontext-Injektion:** SessionStart
  (`capability_briefing.py`) und Prelude-Material überlappen inhaltlich.
  Mittelfristig ein "Context-Butler"-Konzept: Session-Start liefert das
  Standing-Briefing, die Prelude nur promptspezifische Deltas. Erst angehen,
  wenn v9-Daten zeigen, was das Material real beiträgt.
- **Daemon-Rolle klären:** Wird die Prelude Material-only, ist `/classify`
  (Domain) fast wertlos — nur `/search` trägt. Kandidat: Klassifikation ganz
  streichen, Domain nur noch fürs Dedupe/Telemetrie aus Keywords. Weniger
  Code, weniger Thresholds, kein Kalibrierungs-Dauerauftrag (T-8 entfällt).
- **Tracker-Erweiterung (Befund 7b)** bleibt der Schlüssel zu einer echten
  Konsum-Metrik (record_id-Zitate). Owner-Entscheid zu Argument-Logging
  (Privacy) steht weiter aus — erst relevant, wenn v9 läuft und die Frage
  "nützt das Material?" beantwortet werden soll.

## Empfehlung

Phase 1 sofort (ein Nachmittag, reduziert Rauschen ohne Funktionsverlust),
Phase 2.1+2.2 danach als erster echter Test der Timing-These, Phase 3
unabhängig davon — sie hängt nicht an der Advisory-Frage. Nach 2 Wochen
v9-Telemetrie Inventur wiederholen (dieselben drei Auswertungen; sie sind
jetzt reproduzierbar dokumentiert).
