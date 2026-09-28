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

## Runde 2: Präzisions-Kanal (v11-Kandidat) — vorregistriert VOR der Berechnung

Owner-Auftrag 2026-09-28: das Konzept zu einem wirklich hilfreichen Hook
ausbauen. Leitgedanke nach Befund 13: **weniger, aber verlässliche Treffer —
und deren Inhalt direkt einspielen**, weil Verweise kaum nachgelesen werden
(Befund 7: +2–3 pp Atlas-Calls nach Feuern).

**Kandidat "P":** Kandidaten = V1 (normale Suche + Projekt-Partition), dann
Item-Gate: ein Treffer bleibt nur, wenn die Kosinus-Ähnlichkeit zwischen
Prompt (auf 500 Zeichen gekappt, wie `/classify` im Hook) und Treffer-Text
(Snippet) ≥ τ ist. Berechnet über den bestehenden `/classify`-Endpoint
(Labels = Treffer-Snippets) — keine Daemon-Änderung. Treffer ohne Snippet
fallen raus. Präsentation mit Inhalt ändert die Judge-Grundlage nicht (der
Judge sah Hint + Snippet).

**Verfahren:** Split nach `int(sha1(pid), 16) % 2` in Hälfte A (0) und B (1).
τ wird NUR auf A gewählt: kleinstes τ aus {0.20, 0.25, …, 0.60}, bei dem auf A
gilt noise_rate ≤ 0,5 × V0 und useful_rate ≥ V0 − 2 pp (Gate-Pass-Prompts);
erfüllt keines, das τ mit der kleinsten noise_rate unter der useful-Bedingung.
Danach wird τ eingefroren und einmal auf B gemessen.

**Übernahmekriterium auf B (Gate-Pass-Prompts):**
noise_rate(P) ≤ 0,5 × noise_rate(V0) **und** useful_rate(P) ≥ useful_rate(V0) − 2 pp.
Zusätzlich berichtet: Anteil hilfreicher Feuerungen (useful / material) und
die `no_work_signal`-Gruppe (entscheidet, ob das Work-Signal-Gate gelockert
werden kann: nur wenn dort P ebenfalls noise ≤ 0,5 × V0 schafft).

**Bekannte Grenzen:** B hat nur ~70 Gate-Pass-Prompts; ein Bestehen ist ein
starkes Indiz, keine Gewissheit. Bestätigung später an neuen Prompts (der
Korpus wächst täglich), dann mit frischem Judge-Durchlauf.

### Ergebnis Runde 2
τ = 0,30 auf A (Rückfall-Wahl, kein τ halbierte dort das Rauschen); auf B
noise 30 % vs. V0 28 % — **nicht erfüllt**. Explorativ: kein billiges Signal
trennt gut (AUC Kosinus 0,62, Wort-Overlap 0,65; nur innerhalb der Caps
Kosinus 0,75). Ebenfalls explorativ: V1 am **Session-Anfang** useful 29 % vs.
V0 15 % (n=52), bei Fortsetzungen nur +5 pp — am Anfang fehlt Claude der
Projektkontext.

## Runde 3: Session-Start-Projektkarte — vorregistriert VOR der Messung

Die Session-Start-Beobachtung stammt aus denselben Daten, auf denen gesucht
wurde (Forking-Paths-Risiko) — sie gilt erst nach Prüfung an unberührten
Prompts.

**Kandidat "S":** Beim ersten Nutzer-Prompt einer Session (keine vorherige
Assistant-Antwort) wird unabhängig vom Work-Signal-Gate gesucht
(trivial/too_short/Automaten bleiben still) und V1 geliefert (normale Suche +
Projekt-Partition). Fortsetzungen bleiben wie v10.

**Testmenge H:** alle Korpus-Prompts, die in Runde 1/2 NICHT in der
Stichprobe waren, erster Prompt der Session, Gate ∈ {pass, no_work_signal},
≥ 40 Zeichen — Stand 28.09.: 46 Prompts (alle `no_work_signal`, d. h. der
Hook schweigt heute bei ihnen). Neue Paare bewertet derselbe Judge mit
demselben Brief.

**Kriterium auf H:** useful_rate(S) ≥ useful_rate(V0) + 10 pp **und**
noise_rate(S) ≤ useful_rate(S) (hilfreiche Feuerungen mindestens so häufig wie
reine Rausch-Feuerungen). V0 = normale Suche ohne Projekt-Partition auf
denselben Prompts. Bekannte Grenze: n = 46, 10 pp ≈ 5 Prompts.

### Ergebnis Runde 3
V0 useful 9 % / noise 26 %, S useful 26 % / noise 20 % — **bestanden**, als
v11 umgesetzt (NOTES Befund 14).

## Danach

- Sieger per TDD in den Hook, Telemetrie v11, Live-Beleg der Payload-Felder
  `cwd`/`transcript_path` (Hooks-bau-Regel: neue Felder erst live verifizieren).
- Idee 8: Kaltstart des Daemons (59 % Timeouts nach ≥ 1 h Leerlauf) —
  Keepalive im Daemon bevorzugt, weil er auch Pausen innerhalb einer Session
  abdeckt.
