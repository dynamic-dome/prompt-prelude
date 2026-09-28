# Plan: Relevanz messbar machen, dann verbessern (v11-Kandidaten)

Stand 2026-09-28, nach Befund 12. Owner-Auftrag: die empfohlenen Umsetzungen
angehen — Messbasis (Ideen 10 + 11) zusammen mit Projekt-Anker (1) und
Gesprächskontext (2), danach Daemon warm halten (8).

## Warum zuerst Messung

Bisher lief jede Änderung als 2-Wochen-Live-Ära, und die Kernfrage von v9
("passt das Material?") war nie messbar (Befund 7b, 12). Ein Offline-Replay
gegen einen festen Prompt-Korpus plus ein blinder Relevanz-Judge macht
Varianten in Minuten vergleichbar — und nutzt die Transkripte, bevor
`cleanupPeriodDays` (30 Tage) sie löscht.

## Bausteine

1. **Korpus** (`replay/build_corpus.py`): echte Nutzer-Prompts aus den
   Transkripten (`type=user`, Text-Content, `origin.kind=human`,
   `entrypoint=cli`, kein Sidechain, keine Maschinen-Marker) mit `cwd` und
   der letzten Assistant-Antwort davor (gekappt). Lokal gesnapshottet in
   `replay/data/` — **gitignored** (volle Prompts, Repo ist öffentlich).
2. **Replay** (`replay/replay.py`): pro Prompt und Variante Query bauen,
   `/search` am echten Daemon (großzügiges Timeout, kein Kaltstart-Effekt),
   Partitionierung wie im Hook (atlas/-Caps top 3, Mentoren mit Overlap-Gate).
   Varianten-Logik lebt als reine Funktionen in `prompt_prelude.py`, damit der
   Hook den Sieger ohne Neuimplementierung übernimmt.
3. **Judge** (`replay/judge.py`): Paare (Prompt-ID, Record) über alle
   Varianten gepoolt, dedupliziert, gemischt — der Judge sieht NICHT, welche
   Variante ein Paar geliefert hat. Bewertung 0 = irrelevant, 1 = am Rand,
   2 = hilft Claude bei genau diesem Prompt. Verdikte werden pro Paar gecacht,
   neue Varianten brauchen nur neue Paare. Judge = ein einzelner
   Sonnet-Subagent (keine Fable-Fan-outs, Regel #1).

## Varianten

- **V0 Baseline** = v10-Hook-Logik (`extract_query` → `/search` k=12).
- **V1 Projekt-Anker**: Projekt-Slug aus `cwd`; zweite Suche mit Projektname
  in der Query, bis zu 2 Treffer mit Slug im record_id als eigene Partition
  (zusätzlich, nur neue record_ids). *Erster Entwurf verworfen, bevor gejudged
  wurde:* bloßes Umsortieren der normalen Top 12 änderte in 0/154 Prompts mit
  Slug irgendetwas — Projekt-Records stehen dort selten (41/154) und fast nie
  als `atlas/` (1/154). Die Umstellung ist eine Design-Korrektur vor der
  Messung, keine Anpassung an Judge-Ergebnisse.
- **V2 Gesprächskontext**: Query um die signifikantesten Begriffe der letzten
  Assistant-Antwort ergänzt.
- **V3** = V1 + V2.

## Metriken (pro Variante, auf derselben Stichprobe)

- **useful_rate** (primär): Anteil Prompts mit ≥ 1 eingespieltem Item der
  Note 2 — "hat die Prelude hier wirklich geholfen".
- **noise_rate** (Guard): Anteil Prompts mit Material, aber keinem Item ≥ 1.
- precision_strict (Note 2 / alle Items), material_rate — beschreibend.

Getrennt ausgewiesen für Gate-Pass-Prompts und `no_work_signal`-Prompts
(Letzteres beantwortet nebenbei Idee 9: was entgeht dem Work-Signal-Gate?).

## Übernahmekriterium (vorregistriert, vor der ersten Messung)

Eine Variante geht in den Hook (v11), wenn auf den Gate-Pass-Prompts
**useful_rate ≥ Baseline + 10 pp** UND **noise_rate ≤ Baseline + 5 pp**.
Sonst bleibt sie draußen, egal wie plausibel sie klingt. Die 10 pp sind
pragmatisch (gepaarter Vergleich, n ≈ 60), kein Signifikanztest.

**Bekannte Grenzen:** Der Judge beurteilt Relevanz, nicht Konsum — ob Claude
das Material nutzt, bleibt eine separate Frage (Idee 12). Der Judge ist ein
LLM: vor dem Einsatz eine kleine Stichprobe gegen die eigene Einschätzung
gegenprüfen (Befund-7-Lehre: Metrik-Semantik prüfen, nicht nur Zahlen).

## Ergebnis (2026-09-28, NOTES Befund 13)

Kriterium von keiner Variante erfüllt — nichts wird übernommen. Baseline:
useful 15 %, noise 24 % auf Gate-Pass-Prompts. V1 +6 pp (knapp verfehlt),
V2 schadet. Nächster Hebel laut explorativer Aufschlüsselung: Relevanz-Gate
pro Item (Caps-Partition 18 % Präzision) statt Query-Varianten.

## Danach

- Sieger per TDD in den Hook, Telemetrie v11, Live-Beleg der Payload-Felder
  `cwd`/`transcript_path` (Hooks-bau-Regel: neue Felder erst live verifizieren).
- Idee 8: Kaltstart des Daemons (59 % Timeouts nach ≥ 1 h Leerlauf) —
  Keepalive im Daemon bevorzugt, weil er auch Pausen innerhalb einer Session
  abdeckt.
