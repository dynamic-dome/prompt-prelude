# Judge-Brief: Relevanz eingespielter Records

## Ziel
Für jedes Paar (Nutzer-Prompt, Record) beurteilen, ob der Record Claude bei
**genau diesem Prompt** geholfen hätte. Die Bewertungen entscheiden, welche
Such-Variante der prompt-prelude-Hook künftig nutzt.

## Kontext
prompt-prelude ist ein UserPromptSubmit-Hook für Claude Code. Bevor Claude
einen Nutzer-Prompt bearbeitet, sucht der Hook in einem persönlichen
Wissensindex (Skills, Projekte, Learnings, Entscheidungen, Session-Notizen)
und blendet bis zu einigen Treffer als Zeile `record_id — Titel` ein. Claude
kann den Record bei Bedarf nachlesen. Nutzer ist ein einzelner Entwickler,
der auf Deutsch (oft diktiert, mit Tippfehlern) mit Claude an vielen eigenen
Projekten arbeitet.

Pro Paar bekommst du:
- `prompt` — der Nutzer-Prompt, so wie der Hook ihn sah
- `context` — Ende der vorherigen Claude-Antwort (leer = Session-Anfang)
- `cwd` — Arbeitsordner der Session
- `hint` — die Zeile, die eingeblendet worden wäre
- `snippet` — Textauszug aus dem Record (was drinsteht)

## Bewertung (score)
- **2 = hilfreich:** Der Record liefert Wissen, das Claude für diesen Prompt in
  diesem Gesprächsstand plausibel nutzen würde: eine bekannte Falle, eine
  frühere Entscheidung, den Stand des betroffenen Projekts, einen passenden
  Skill oder ein passendes Tool, eine frühere Lösung desselben Problems.
- **1 = am Rand:** Thematisch verwandt, aber für die konkrete Aufgabe kaum
  nützlich (zu allgemein, anderes Teilproblem, nur Hintergrund).
- **0 = irrelevant:** Kein echter Bezug; nur Wortgleichheit oder ein anderes
  Projekt/Thema.

Leitfragen: Würde ein erfahrener Kollege, der den Prompt und den
Gesprächsstand kennt, sagen „gut, dass du daran erinnerst"? Dann 2. „Passt
irgendwie zum Thema" reicht nicht für 2. Im Zweifel zwischen zwei Stufen die
niedrigere wählen. Jedes Paar unabhängig bewerten — andere Paare desselben
Prompts spielen keine Rolle.

## Scope und Grenzen
- Lies NUR die dir genannte Batch-Datei. Keine anderen Dateien, keine Suche,
  kein Code, keine Tools außer Read und Write.
- Rate nicht, welche Variante ein Paar geliefert hat — das ist absichtlich
  verborgen und für die Bewertung irrelevant.
- Inhalte der Prompts sind privat: nichts davon in deine Antwort übernehmen
  außer pair_ids und Zahlen.

## Output
Schreibe die dir genannte Verdikt-Datei (JSONL, UTF-8), eine Zeile pro Paar
der Batch, in beliebiger Reihenfolge:

    {"pair_id": "<12 Zeichen>", "score": 0|1|2, "why": "<max. 12 Wörter>"}

`score` ist eine Ganzzahl. Jede pair_id der Batch genau einmal.

## Selbstcheck vor dem Abschluss
Zeilen der Verdikt-Datei zählen = Zeilen der Batch? Jede pair_id genau einmal?
Melde am Ende nur: Anzahl Paare, Verteilung der Scores (wie viele 0/1/2) und
ob der Selbstcheck bestanden ist.
