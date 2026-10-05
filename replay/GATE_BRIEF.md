# Gate-Brief: Welche Treffer helfen jetzt wirklich?

## Ziel
Für jeden Nutzer-Prompt aus einer Liste von Kandidaten die wenigen auswählen, die
Claude bei **genau diesem Prompt in genau diesem Gesprächsstand** helfen würden.
Meistens ist die richtige Antwort: keiner.

## Kontext
Ein Hook sucht vor jedem Prompt in einem persönlichen Wissensindex (Skills,
Projekte, Learnings, Entscheidungen, Session-Notizen) und blendet Treffer ein.
Bisher wählen feste Regeln aus, und die meisten eingeblendeten Treffer sind
Rauschen. Du bist das Gate dazwischen: Du siehst, was die Suche gefunden hat, und
lässt nur durch, was wirklich nützt. Der Nutzer ist ein einzelner Entwickler, der
auf Deutsch (oft diktiert, mit Tippfehlern) mit Claude an vielen eigenen Projekten
arbeitet.

Pro Zeile der Batch-Datei bekommst du:
- `pid` — Kennung des Prompts
- `prompt` — der Nutzer-Prompt
- `context` — Ende der vorherigen Claude-Antwort (leer = Session-Anfang)
- `cwd` — Arbeitsordner der Session
- `candidates` — Liste aus `cid`, `hint` (Kennung und Titel des Records) und
  `snippet` (Textauszug)

## Auswahlregel
Behalte einen Kandidaten nur, wenn ein erfahrener Kollege, der Prompt und
Gesprächsstand kennt, sagen würde: „gut, dass du daran erinnerst". Das trifft zu
bei einer bekannten Falle, einer früheren Entscheidung, dem Stand genau des
betroffenen Projekts, einem passenden Skill oder Tool oder einer früheren Lösung
desselben Problems.

Nicht behalten:
- thematisch verwandt, aber für die konkrete Aufgabe nicht nützlich;
- nur Wortgleichheit;
- ein anderes Projekt als das, um das es gerade geht;
- allgemeiner Hintergrund, den Claude für diesen Schritt nicht braucht.

Höchstens 2 Kandidaten je Prompt. Im Zweifel keinen. Ein leeres Ergebnis ist ein
gutes Ergebnis, wenn nichts wirklich passt. Jeden Prompt unabhängig bewerten.

## Scope und Grenzen
- Lies NUR diesen Brief und die dir genannte Batch-Datei. Keine anderen Dateien,
  keine Suche, kein Code, keine Tools außer Read und Write.
- Inhalte der Prompts sind privat: nichts davon in deine Antwort übernehmen
  außer pids, cids und Zahlen.

## Output
Schreibe die dir genannte Ergebnis-Datei (JSONL, UTF-8), genau eine Zeile pro
Prompt der Batch, in beliebiger Reihenfolge:

    {"pid": "<Kennung>", "keep": ["<cid>", ...], "why": "<max. 12 Wörter>"}

`keep` ist eine Liste mit 0, 1 oder 2 cids aus den Kandidaten genau dieses Prompts.

## Selbstcheck vor dem Abschluss
Zeilen der Ergebnis-Datei = Zeilen der Batch? Jede pid genau einmal? Kein `keep`
mit mehr als 2 Einträgen? Melde am Ende nur: Anzahl Prompts, wie viele davon mit
leerem `keep`, wie viele behaltene Kandidaten insgesamt, Selbstcheck bestanden
ja oder nein.
