# Design: Zuverlässig das Richtige einspielen (Prelude v2)

Stand: 2026-10-19 · Owner: Dominic · Autor: Claude (Opus 5.5), aus dem Brainstorming in der DCO-Sitzung vom 2026-10-19
Status: Entwurf zur Freigabe · Bezug: DCO-Todo #8953 (Hub-Tags), #9789 (Atlas-Eval), Befund 17 (`NOTES-live-findings.md`)

## 1. Zweck und Warum

Vor jeder Aufgabe und zu Beginn einer Sitzung sollen Claude-Code-Agenten **die richtigen Learnings, die passenden
Skills und die passenden Capabilities** im Kontext haben, damit bekannte Stolpersteine nicht wiederholt werden und das
richtige Werkzeug genutzt wird. Owner-Wortlaut sinngemäß: „Learnings zuverlässig einspielen, Skills und Capabilities
vorschlagen — da sollte eigentlich nichts dran zu rütteln sein.“ Ob das Modell die Hinweise danach umsetzt, ist der
zweite Schritt; dieses Design sorgt dafür, dass das Richtige zur richtigen Zeit im Kontext steht.

Dass der Owner selbst in Atlas stöbert, ist zweitrangig (Owner, 2026-10-19).

## 2. Ist-Zustand (gemessen 2026-10-19, nur lesend)

Quellen: Recon-Workflow `wf_da0fd154-e74`, Artefakte unter `~/AI/artifacts/atlas-injection-recon/` (lokal, enthält
Prompt-Paraphrasen, nicht ins Repo kopieren), Hub-Design-Workflow `wf_cf8965ef-a09` (`~/AI/artifacts/atlas-hub-design/`).

- **Nur `prompt_prelude.py` spielt ein** (`~/.claude/settings.json`, `UserPromptSubmit`). Der Mod
  `claude-mods/prelude-gate` läuft seit 2026-10-05 nur im Schatten (`plugin.json`: Modi `shadow|off`, „Spielt nichts
  ein“; 881 Protokolle unter `replay/data/shadow/`). Codex bekommt nichts (Prompt-Hooks in `~/.codex/config.toml`
  deaktiviert).
- **Zündung:** etwa 5 % der Prompts (~6/Tag); strenge Gates (`no_work_signal`, `too_short`, `low_domain_confidence`).
- **Ausbeute:** im Mittel < 1 Learning pro Zündung, nur als Titelzeile (60 Zeichen), in höchstens 2 „Frühere
  Fälle“-Plätzen, die sich Learnings mit Wiki-Sitzungsnotizen teilen; 3 Capability-Plätze.
- **Sitzungsstart:** keine Atlas-Suche; im Kontext stehen nur die statischen Listen aus `CLAUDE.md`/`MEMORY.md`.
- **Qualität, 39 echte Fälle / 103 eingespielte Plätze** (ein Opus-Gutachter, nachgezählt): relevant 8 %, wenig
  Nutzen 26 %, Rauschen 63 %, doppelt 3 %; in 33 von 39 Fällen kein relevanter Platz, in 18 wäre „nichts“ besser
  gewesen; Capability-Plätze 0 relevant von 43; in 22 von 39 Fällen lag ein klar besseres Learning in Atlas.
- **Ursachen nach Wirkung:**
  1. Anfrage = die ersten 12 Wörter (≥ 4 Buchstaben, ohne Stoppwörter) des Prompts (`extract_query`), oft Füllwörter,
     Pfade, Diktierfehler. Richtiges Learning unter den Top 40: 1/16 mit Hook-Anfrage, 6/16 mit gezielter Anfrage.
  2. Kein eigener Learnings-Topf: Learnings konkurrieren mit ~56.000 Wiki-/Todo-Abschnitten. Mit Quellenfilter und
     gezielter Anfrage: 15/16 unter den Top 40, 10/16 unter den Top 5.
  3. Allowlist sperrt Claude-eigene Erinnerungen (`native-*`), Codex-Learnings (`codex-native/`), `agent-lab/`,
     `membrain/` (`MENTOR_PREFIXES`); 9 der 22 besseren Kandidaten lagen in `native-*`.
  4. SQLite-Notweg (Daemon-Budget 0,5 s/1,2 s): lieferte die Hälfte der Einspielungen, 75 % Rauschen.
  5. Keine Relevanzhürde: es wird eingespielt, sobald irgendetwas gefunden ist.
- **Nicht die Ursache** (gemessen, nachrangig): Hub-Tag-Alphabet (6 % der Plätze), Kopien (3 Plätze), themenlose
  Learnings (nicht messbar schädlich). Die Kappung der Hub-Tags ist verworfen (#9789).
- **Schatten-Gate (Befund 17, 125 Prompts):** hilfreich 17 % statt 4 %, Rauschen 10 % statt 24 %, Treffsicherheit je
  Eintrag 26 % statt 7 %. Besser, aber weit vom Ziel: der Pool entsteht aus derselben 12-Wort-Anfrage.

## 3. Ziele

- **Learnings:** Für jede echte Aufgabe landen die ein bis zwei Learnings im Kontext, die für genau diese Aufgabe
  zählen, mit ihrem Kernsatz (was zu tun ist und warum), nicht nur dem Titel.
- **Skills:** Kommen viele in Frage, wird nur der eine oder höchstens zwei passendste vorgeschlagen, je mit Halbsatz-
  Begründung.
- **Capabilities (Werkzeuge, MCP-Server):** dasselbe Prinzip, nur die passendsten.
- **Prompt-Klärung nur im Ausnahmefall:** ist ein Prompt unverständlich (z. B. Diktat-Kauderwelsch), darf eine kurze
  Klarstellung „vermutlich gemeint: …“ **neben** dem Prompt stehen; sie ersetzt ihn nie. Bei verständlichen Prompts
  nichts.
- **Sitzungsstart:** einmal gezielt passend zur ersten Aufgabe einspielen (statt nur der starren Listen).
- **Zündung:** bei jedem echten Arbeitsprompt in Claude Code, nicht nur bei 5 %.
- **Messbar besser:** siehe Abschnitt 8.

## 4. Nicht-Ziele

- Projektkontext von sich aus nachliefern — den sucht Claude selbst (Owner, 2026-10-19).
- Eine Stöber-Oberfläche für den Owner; die Funktion „Verwandte“ (`related_records`) verbessern.
- Codex-Einspielung (dort ist der Prompt-Hook heute aus) — späterer Schritt.
- Atlas-Graph-Umbau (Gewichtung, Hub-Kappung, Tag-Hierarchie) — nach Messung nachrangig.
- Mehr Plätze: bei schlechter Trefferqualität bringen mehr Plätze nur mehr Rauschen.

## 5. Anti-Ziele (würde den Owner „zusammenzucken“ lassen)

**Eine Einspielung führt Claude in eine falsche Richtung, obwohl er ohne sie richtig gelaufen wäre** — „wenn es mehr
Ärger verursacht, als es hilft“ (Owner). Daraus folgen harte Leitplanken (Abschnitt 7) und eine eigene Kennzahl
„irreführend“, die vor dem Live-Gang praktisch null sein muss.

Weitere Anti-Ziele: Wartezeit über 4 s; stilles Ausweichen auf eine schwache Notsuche; Rauschen statt „nichts“.

## 6. Prinzip: Verstehen → gezielt suchen → prüfen → knapp einspielen → messen

1. **Verstehen — ohne Modellaufruf** (Owner-Entscheid 2026-10-19, Zeitbudget). Gemessen in 883 Schatten-Protokollen:
   ein Haiku-Aufruf braucht 2,6 s im Median und 4,9 s im p95, die Kandidatensuche 0,5 s/1,6 s; zwei Modellaufrufe
   nacheinander passen nicht ins Budget. Deshalb baut eine **regelbasierte Mehrfachsuche** die Anfragen: Schlüsselbegriffe
   aus Prompt und Ende der letzten Antwort (Pfade, Füllwörter, Diktat-Rauschen entfernt; seltene, fachliche Begriffe
   bevorzugt) statt der ersten 12 Wörter, je Topf eine eigene Anfrage. Ersetzt `extract_query`. Das Modell kommt nur
   einmal zum Zug, im Schritt Prüfen.
2. **Gezielt suchen, getrennte Töpfe.** Je Topf eine Anfrage an den Atlas-Daemon `/search`. Der Daemon wird dafür
   erweitert (Owner-Freigabe 2026-10-19, siehe Abschnitt 10): mehrere Quellen in einer Suche, Präfixfilter, `as_of`
   für den Zeitreise-Filter der Messbank, Datum und `superseded_by` im Ergebnis:
   - *Learnings:* alle Gedächtnisquellen (agent-memory-Learnings und -Patterns, Claude-eigene Erinnerungen `native-*`,
     Codex-Learnings, membrain), Wiki-Sitzungsnotizen nur nachrangig.
   - *Skills* und *Capabilities:* die Capability-Karten (`atlas/…`).
   - Projekt = weicher Vorrang, kein Ausschluss.
3. **Prüfen — der einzige Modellaufruf.** Haiku sieht Prompt, Ende der letzten Antwort, Arbeitsordner und je Topf die
   Kandidaten und behält 0 bis 2 je Topf, mit einer Begründung je Eintrag. „Nichts“ ist ausdrücklich erlaubt und im
   Zweifel Pflicht. Im selben Aufruf entscheidet es, ob der Prompt eine Klärung „vermutlich gemeint: …“ braucht.
   Baut auf dem Gate-Prompt aus `prelude-gate` auf.
4. **Knapp einspielen.** Ein kompakter Block im Kontext:
   - Learnings mit Kernsatz (Regel + Warum, aus dem Record, gekürzt), Quelle und Datum.
   - Skills/Capabilities als Vorschlag „nutze X, weil …“.
   - optional „vermutlich gemeint: …“.
   - Rahmung als **Hinweis zur Prüfung, nicht als Anweisung**.
   - Eine sichtbare Zeile für den Owner (`systemMessage`), was eingespielt wurde.
5. **Messen.** Jede Einspielung wird protokolliert (was, warum, Laufzeit, Abbruchgrund); die Messbank (Abschnitt 8)
   entscheidet über jede Änderung.

Fortsetzungs-Prompts („mach weiter“, „ja“) bekommen nur dann etwas, wenn sich das Thema gegenüber dem letzten Prompt
geändert hat oder noch nichts eingespielt wurde (Claudes Entscheidung, Builder darf präzisieren).

## 7. Leitplanken gegen Irreführung

- Nie eingespielt werden: abgelaufene (`valid_to`), überholte (`superseded_by`), abgelehnte (`review=rejected`)
  Records; bei Varianten desselben Learnings die neueste.
- Rahmung: „Hinweise aus früheren Fällen — prüfen, nur nutzen, wenn sie zur Aufgabe passen.“
- Jeder eingespielte Eintrag trägt seine Begründung vom Prüfer; fehlt sie oder ist sie schwach, entfällt der Eintrag.
- Widerspricht ein Learning dem Prompt erkennbar, wird es nicht eingespielt (Prüfer-Regel).
- Schalter: Modus `off|shadow|live` für den Owner; Rückweg jederzeit `shadow`.
- Kein Live-Gang ohne bestandene Messbank inkl. Kennzahl „irreführend“.

## 8. Messbank und Erfolgskriterien

**Grundstock:** die 39 bewerteten Fälle (`~/AI/artifacts/atlas-injection-recon/judge/judgments.json`, `sample.json`):
Positive = die 22 „besser fehlend“-Kandidaten + 8 relevante Treffer; harte Negative = 65 Rausch-Plätze; 18 Fälle
„erwartet: nichts einspielen“; Zeitreise-Filter (keine Records nach dem Prompt-Zeitpunkt). Erweiterung um die 881
Schatten-Protokolle als Silber-Labels; ~10 Fälle vom Owner kalibrieren lassen, ~30 % als Hold-out.
Die Bank liegt lokal (Prompt-Inhalte des Owners); ins Repo nur Code und anonymisierte Kennzahlen. Für Replays
braucht sie die Original-Prompts aus den Transkripten (vorhanden für 114 von 122 Ereignissen; `sample.json` hat nur
Paraphrasen).

**Wer beurteilt** (Owner-Entscheid 2026-10-19): ein automatischer Gutachter mit ausdrücklich `model: opus` (Reviews
nach Owner-Regel auf Opus, nie Fable) bewertet jeden neuen Treffer als relevant / wenig Nutzen / Rauschen / doppelt /
**irreführend**. Der Owner kalibriert einmal etwa 10 Fälle und sichtet vor dem Live-Gang alles, was der Gutachter als
„irreführend“ markiert. „0 irreführend“ heißt: 0 vom Owner bestätigt.

**Skills und Capabilities** haben im Grundstock keine positiven Beispiele (0 relevante von 43 Capability-Plätzen). Sie
bekommen eigene Bewertungen durch denselben Gutachter und gehen erst live, wenn sie die Ziele dieser Tabelle in ihrem
Topf erreichen (Claudes Entscheidung).

**Erfolg gegenüber heute (alter Hook) auf der Bank:**

| Kennzahl | heute | Ziel |
|---|---|---|
| relevante Plätze | 8 % | > 50 % |
| Rauschen | 63 % | < 15 % |
| Fälle mit fehlendem klar besserem Learning | 56 % | < 20 % |
| „nichts“ richtig erkannt (18 Fälle) | — | ≥ 80 % |
| **irreführend** (würde in falsche Richtung lenken) | nicht erhoben | **0 in der Bank, im Live-Betrieb Stichprobe ≤ 2 %** |
| Laufzeit | — | harte Grenze 4 s; Abbruchquote („zu langsam, nichts eingespielt“) wird gemessen, Startwert erwartet um 10 % |

## 9. Ausrollen

1. **Schatten v2:** neue Pipeline läuft parallel und schreibt nur mit (wie heute das Gate); alter Hook bleibt live.
2. **Bank + Schatten auswerten:** Ziele aus Abschnitt 8 erreicht?
3. **Live, Learnings zuerst (Owner-Gate):** der neue Weg spielt Learnings ein und **ersetzt den alten Hook beim
   Einspielen ganz** (Claudes Entscheidung): dessen Projektkarte widerspricht dem Nicht-Ziel „Projektkontext nicht von
   sich aus“, seine regelbasierten `SKILL-ROUTING`-Zeilen würden Skills doppelt vorschlagen. Der alte Hook bleibt als
   Code für den Rückweg erhalten, spielt aber nichts mehr ein.
4. **Skills und Capabilities live**, sobald ihr Topf die Ziele aus Abschnitt 8 erreicht (Owner-Gate).
5. 1–2 Wochen Live-Stichprobe je Schritt; danach Entscheid über den endgültigen Abbau des alten Hooks.

## 10. Rahmenbedingungen

- Windows 11, Claude Code (Mod-System `claude-mods`, `CLAUDE_CODE_PLUGIN_DIRS`), Atlas-Daemon `127.0.0.1:7801`.
- Zeitbudget pro Prompt **harte Grenze 4 s** gesamt (Owner, 2026-10-19, Option a): nach 4 s abbrechen, nichts
  einspielen, Grund protokollieren — kein stilles Ausweichen auf die SQLite-Notsuche. Die Abbruchquote ist eine
  Kennzahl (Abschnitt 8).
- Modellwahl: ein Haiku-Aufruf zum Prüfen (gemessen); Kosten gering.
- **Atlas-Daemon darf erweitert werden** (Owner-Freigabe 2026-10-19): additive Änderungen an `/search` im Repo
  `agent-memory-atlas` (mehrere `source_system`-Werte, Präfixfilter auf `record_id` — intern existiert
  `record_id_prefixes` in `query_engine.query` —, `as_of`, Rückgabe von Datum und `superseded_by`). Bestehende Aufrufer
  bleiben unverändert gültig. Live erst nach Aktualisierung des Runtime-Checkouts und Daemon-Neustart (Owner-Gate).
- Keine Schreibzugriffe auf Atlas-Stores oder den Index; der Pfad liest nur.
- Owner-Regeln: Test-DB-Isolation vor jedem pytest; Windows-Subprocess-Regeln; keine Pushes ohne Owner.

## 11. Dem Builder überlassen

- Die Regeln der Mehrfachsuche (Begriffsauswahl, Anzahl Anfragen je Topf) — gemessen an der Messbank.
- Konkrete Kandidatenzahl je Topf, Kürzung des Kernsatzes, genaues Blockformat.
- Wie der Kernsatz aus dem Record gewonnen wird (Feld, erster Absatz oder Prüfer-Zusammenfassung).
- Technische Einbettung: Erweiterung von `prelude-gate` um `live` und v2-Pipeline (Claudes Entscheidung: auf dem
  Gate aufbauen) und die Abgabe der Partitionen im alten Hook.
- Aufbau der Messbank als Werkzeug im `replay/`-Bereich.

## 12. Entscheidungen, die als Claudes Entscheidung markiert sind

- Ablage dieses Designs im `prompt-prelude`-Repo.
- Aufbauen auf `prelude-gate` statt Neubau; alter Hook gibt erst nach bestandener Messung ab.
- Fortsetzungsregel (Abschnitt 6, letzter Absatz).
- Learnings-Topf schließt Wiki-Sitzungsnotizen nicht aus, stuft sie nur nach.
- Skills und Capabilities gehen erst nach eigener Bewertung live, Learnings früher (Abschnitte 8 und 9).
- Der alte Hook wird beim Live-Gang ganz ersetzt, nicht nur teilweise (Abschnitt 9).
- „Sitzungsstart“ = der erste Prompt einer Sitzung, mit etwas mehr Plätzen; die festen Listen in `CLAUDE.md`/`MEMORY.md`
  (generiert von regel_sync und der agentic-os-Bridge) bleiben unangetastet.

## 13. Owner-Entscheide aus der Prüfrunde (2026-10-19)

1. Zeitbudget: harte Grenze 4 s, bei Überschreitung nichts einspielen und die Quote messen (Option a).
2. Messbank: Opus-Gutachter plus Owner-Kalibrierung (~10 Fälle) und Owner-Sichtung aller „irreführend“-Markierungen
   vor dem Live-Gang.
3. Atlas-Daemon darf additiv erweitert werden; Neustart bleibt Owner-Gate.
