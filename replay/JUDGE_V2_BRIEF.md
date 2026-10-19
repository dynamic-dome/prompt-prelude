# Judge-Brief v2: behaltene Einträge bewerten (blind)

Modell: **opus** (ausdrücklich; nie ein anderes). Dieser Brief ist für den Gutachter der Messbank.

## Ziel
Für jeden Eintrag, den das Gate behalten hat, beurteilen, ob er Claude bei **genau diesem Prompt
in diesem Gesprächsstand** geholfen hätte oder ob er geschadet hätte. Die Urteile entscheiden, ob
die neue Einspielung live gehen darf; ein einziger bestätigter `misleading`-Eintrag blockiert sie.

## Kontext
Ein Hook sucht vor jedem Prompt in einem persönlichen Wissensindex (Learnings, Skills,
Werkzeug-Karten) und spielt die vom Gate behaltenen Einträge als Hinweise ein. Nutzer ist ein
einzelner Entwickler, der auf Deutsch (oft diktiert, mit Tippfehlern) mit Claude an vielen
eigenen Projekten arbeitet.

Pro Zeile der Batch `judge_batches/NN.jsonl` bekommst du:
- `case_id`, `rid` — Kennung des Falls und des Eintrags (zusammen eindeutig)
- `prompt` — der Nutzer-Prompt
- `context` — Ende der vorherigen Claude-Antwort (leer = Sitzungsanfang)
- `cwd` — Arbeitsordner der Sitzung
- `summary`, `snippet` — Kernsatz und Textauszug des Eintrags (was eingespielt worden wäre)

## Bewertung (label)
Genau eines je Eintrag:
- **relevant** — liefert Wissen, das Claude für diesen Prompt plausibel nutzen würde: bekannte Falle,
  frühere Entscheidung, Stand des betroffenen Projekts, passender Skill oder passendes Werkzeug,
  frühere Lösung desselben Problems. Prüfstein: ein erfahrener Kollege, der Prompt und
  Gesprächsstand kennt, sagt „gut, dass du daran erinnerst“.
- **marginal** — thematisch verwandt, für die konkrete Aufgabe kaum nützlich (zu allgemein, anderes
  Teilproblem, nur Hintergrund).
- **noise** — kein echter Bezug; nur Wortgleichheit oder anderes Projekt/Thema.
- **duplicate** — sagt dasselbe wie ein anderer behaltener Eintrag **desselben Falls** (gleiche
  Aussage, anderes Dokument) oder etwas, das der Prompt oder der Gesprächsstand bereits enthält.
  Der zuerst genannte Eintrag bleibt `relevant`/`marginal`, die Wiederholung ist `duplicate`.
- **misleading** — würde Claude von einem richtigen Weg abbringen: widerspricht dem, was der Prompt
  oder der Gesprächsstand erkennbar will; gilt für ein anderes Projekt, eine andere Version oder
  einen anderen Fall und würde fälschlich übertragen; ist erkennbar überholt, revidiert oder falsch;
  oder legt einen Werkzeug-/Skill-Einsatz nahe, der die Aufgabe in die falsche Richtung lenkt.
  Wortgleichheit allein ist `noise`, nicht `misleading`. Im Zweifel zwischen `misleading` und
  `noise` wähle `misleading` nur, wenn du den konkreten falschen Weg benennen kannst (im `why`).

Im Zweifel zwischen zwei Stufen von `relevant` bis `noise` die niedrigere wählen. Jeden Eintrag
unabhängig bewerten, außer für `duplicate`.

## Blind
Du weißt absichtlich nicht, aus welcher Quelle oder Messvariante ein Eintrag stammt, ob er alt oder
neu ist oder ob ein Gold-Label existiert. Rate das nicht und lass es nicht in das Urteil einfließen.
Bewerte nur Prompt, Gesprächsstand und Eintragstext.

## Scope und Grenzen
- Lies NUR diesen Brief und die dir genannte Batch-Datei. Keine anderen Dateien, keine Suche, kein
  Code, keine Tools außer Read und Write.
- Inhalte der Prompts und Einträge sind privat: nichts davon in deine Antwort an den Auftraggeber
  übernehmen außer `case_id`s, `rid`s und Zahlen.

## Ausgabe
Schreibe die dir genannte Datei `judge_out/NN.jsonl` (JSONL, UTF-8), genau eine Zeile je Zeile der
Batch, in beliebiger Reihenfolge:

    {"case_id": "<Kennung>", "rid": "<Eintrag>", "label": "relevant|marginal|noise|duplicate|misleading", "why": "<max. 12 Wörter>"}

## Selbstcheck vor dem Abschluss
Zeilen der Ausgabe = Zeilen der Batch? Jedes Paar `case_id` + `rid` genau einmal? Jedes `label` eines
der fünf erlaubten Wörter? Melde am Ende nur: Anzahl Einträge, Verteilung der Labels (wie viele je
Label), die `case_id` + `rid` jedes `misleading`-Urteils, Selbstcheck bestanden ja oder nein.
