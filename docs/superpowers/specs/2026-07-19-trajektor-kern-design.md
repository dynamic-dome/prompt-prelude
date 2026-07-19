# Trajektor-Kern — PostToolUse-Drift-Beobachter (T-12)

*Design-Spec 2026-07-19 · beschlossen im Brainstorming (User-approved) · Roadmap Punkt 2
(wiki/todos/2026-07-18-prompt-prelude-erweiterungen.md) · Basis: agent-lab-vault
synthesis/synth-trajektor-context-router.md, bewusst abgespeckt.*

## Ziel

prompt-prelude ist blind für die 30–80 Tool-Calls zwischen den User-Prompts. Der
Trajektor-Kern beobachtet den Tool-Call-Strom per PostToolUse-Hook, misst Drift gegen
den zuletzt erteilten Auftrag (Goal-Anchor) deterministisch und injiziert bei
bestätigter Drift eine Reframing-Zeile — non-blocking, fail-soft, ohne Daemon-Calls.

## Scope-Entscheidungen (User-Gates)

1. **Fire-Payload: nur Reframing-Zeile.** Deterministischer Diagnose-Satz + 2–3
   Anchor-Kernpunkte. Keine RAG-Nachladung im Hot-Path (→ Folge-Task T-12b).
2. **Anchor-Politik: letzter Arbeits-Prompt.** Jeder User-Prompt, der das
   Präzisions-Gate passiert, setzt den Anchor neu. Kurz-Zurufe/Skips re-anchorn nicht.
   Legitimer Themenwechsel des Users erzeugt damit keinen Fehlalarm.
3. **Bewusst draußen (YAGNI):** Serendipitäts-Budget, Cockpit-UI, RAG-Nachladung,
   `turn_pressure`-Komponente, Embedding-basierte Drift. Phase-0-Fixes der Synthese
   sind bereits gebaut (Dedupe domain+phase, Planning-Vokabular).

## Architektur: Schwester-Modul im selben Repo (Ansatz A)

- **`trajektor.py`** (neu, neben `prompt_prelude.py`): der PostToolUse-Hook.
  Importiert geteilte Bausteine aus `prompt_prelude` (Keyword-Map, `topic_signature`,
  Telemetrie-/State-Muster). Eigene Datei — der bewährte Prompt-Pfad bleibt unberührt.
- **Anchor-Schreiber** (~20 Zeilen Erweiterung in `prompt_prelude.py`): beim Gate-Pass
  wird `anchor-<sid>.json` geschrieben — Prompt-Preview, Domain, Phase, signifikante
  Tokens (≥4 Zeichen, wie Mentor-Gate), erwähnte Dateipfade.
- **Registrierung:** settings.json PostToolUse → `python trajektor.py`, timeout 2 s.

## Datenfluss & Drift-Heuristik

1. **Fenster:** rollendes Fenster der letzten K=15 Tool-Calls (Tool-Name + berührte
   Pfade aus `tool_input`: `file_path`, `command`, `pattern`, …), persistiert als
   `trajektor-window-<sid>.json`.
2. **Drift-Score** = gewichtete Summe aus drei deterministischen Komponenten:
   - `token_shift`: 1 − Jaccard-Overlap zwischen Anchor-Tokens und Fenster-Tokens
     (Pfadsegmente + Bash-Kommandowörter)
   - `path_divergence`: Anteil der Fenster-Pfade außerhalb der Anchor-Verzeichnisse
   - `phase_flip`: Tool-Mix-Phase (Read/Grep=explore, Edit/Write=build,
     Bash+Test-Runner=verify) weicht von der Anchor-Phase ab → fester Beitrag
3. **Hysterese:** `fire` ≥ 0.65, `clear` ≤ 0.45; nach einem Fire erst wieder scharf,
   wenn der Score unter `clear` fiel; zusätzlich Cooldown von 10 Tool-Calls.
4. **Fire-Output:** non-blocking `additionalContext` (Reframing-Zeile + Anchor-
   Kernpunkte) + sichtbare `systemMessage` (T-31-Konvention).

## State, Overhead, Fail-Soft

- State im bestehenden prompt-prelude-State-Dir, `_safe_session`-Härtung +
  7-Tage-Cleanup wie beim `fired`-State. Kein neues Verzeichnis-Schema.
- stdin als UTF-8-Bytes (v4-Härtung); fehlende Felder → leer, nie KeyError.
- **Overhead-Budget: < 150 ms pro Call, gemessen** (Median über 20 Aufrufe;
  CI-Assert hart < 500 ms). Hot-Path: reines Python + 1× JSON-Read + 1× JSON-Write.
- **Ohne Anchor kein Urteil:** Fenster wird gepflegt, kein Score/Fire,
  Telemetrie-Status `no_anchor`.
- **Session-Cap: max. 3 Fires pro Session** (Alert-Fatigue = Top-Risiko der Synthese).
- Fail-soft total: jede Exception → Exit 0 ohne Output; Telemetrie-Write in
  try/except. Advisory-Kanal blockiert NIE.
- Telemetrie: `trajektor.jsonl`, eigene Ära **t1** (Ären-Politik D7 gilt — nie mit
  prelude-Ären mischen).

## Tests & Definition of Done

- TDD; bestehende 187 Tests bleiben grün. Neue Tests in `test_trajektor.py`,
  Anchor-Schreiber-Tests in `test_prompt_prelude.py`.
- Tabellen-getriebene Fixture-Szenarien: on-track (kein Fire — eigener Test) /
  langsame Drift / harter Domain-Sprung / Phasenwechsel / Re-Anchor durch neuen
  Prompt → je erwarteter Fire/Clear-Verlauf.
- Hysterese-Tests: zappelnder Score → genau 1 Fire; Cooldown respektiert;
  Session-Cap greift.
- E2E-Subprocess-Test: echtes stdin-JSON → valides Hook-Output-JSON;
  Determinismus: gleicher Fixture-Strom → identisches `trajektor.jsonl` (ohne ts).
- Overhead-Messung asserted (s. o.).
- **DoD:** Suite grün · Live-Smoke in echter Session (Registrierung aktiv,
  `trajektor.jsonl` entsteht, kein Fire bei fokussierter Arbeit) · README-Abschnitt
  (Registrierung + Ära t1) · Schwellen-Kalibrierung ist explizit NICHT Teil der DoD
  (nachgelagerte Telemetrie-Auswertung analog T-11).
