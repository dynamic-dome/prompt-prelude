# Gate-Brief v2: das Relevanz-Gate für eine Batch-Datei spielen

## Ziel
Für jeden Fall einer Batch-Datei genau das tun, was das Gate vor dem Coding-Assistenten live tut:
aus den Kandidaten die wenigen auswählen, die bei **genau diesem Prompt in genau diesem
Gesprächsstand** helfen. Meistens ist die richtige Antwort: keiner.

Du bist das Gate, nicht ein Prüfer des Gates. Deine Antworten werden später mit dem Parser des
Live-Mods ausgewertet; es zählt, was Haiku live antworten würde.

## Eingabe
Die dir genannte Batch-Datei `gate_batches/NN.jsonl` (JSONL, UTF-8), eine Zeile je Fall:
- `case_id` — Kennung des Falls
- `system` — die Gate-Anweisung (`GATE_SYSTEM_V2`)
- `user` — der Text, den das Gate live als Eingabe bekommt: Prompt, Ende der vorherigen Antwort,
  Arbeitsordner und die Kandidaten je Topf (`cid | Kennung | Kernsatz`)

Weitere Felder der Zeile (`pools`, `prompt`, ...) sind Maschinenmaterial: nicht auswerten.

## Vorgehen je Fall
1. Wende genau `GATE_SYSTEM_V2` aus dem Feld `system` auf den Text im Feld `user` an. Die Anweisung
   nicht umformulieren, nicht ergänzen, keine eigenen Regeln dazu erfinden.
2. Antworte so, wie die Anweisung es verlangt: **ein JSON-Objekt in einer Zeile**
   (`learnings`, `skills`, `capabilities`, `clarify`). Nur `cid`s aus den Kandidaten dieses Falls;
   jedes behaltene `cid` mit `why`. Leere Listen sind eine gute Antwort.
3. Jeden Fall unabhängig bewerten; was bei anderen Fällen behalten wurde, spielt keine Rolle.

## Scope und Grenzen
- Lies NUR diesen Brief und die dir genannte Batch-Datei. Keine anderen Dateien, keine Suche,
  kein Code, keine Tools außer Read und Write. Insbesondere keine Messbank-Dateien neben der Batch.
- Inhalte der Prompts sind privat: nichts davon in deine Antwort an den Auftraggeber übernehmen
  außer `case_id`s und Zahlen.

## Ausgabe
Schreibe die dir genannte Datei `gate_out/NN.jsonl` (JSONL, UTF-8), genau eine Zeile je Fall der
Batch, in beliebiger Reihenfolge:

    {"case_id": "<Kennung>", "reply": "<die Ein-Zeilen-JSON-Antwort als String>"}

`reply` ist die Antwort des Gates unverändert als Text (also ein JSON-Objekt als String, nicht
verschachtelt).

## Selbstcheck vor dem Abschluss
Zeilen der Ausgabe = Zeilen der Batch? Jede `case_id` genau einmal? Jede `reply` ein einzeiliges
JSON-Objekt mit den Schlüsseln `learnings`, `skills`, `capabilities`, `clarify`? Nirgends mehr als
die erlaubte Zahl je Topf? Melde am Ende nur: Anzahl Fälle, wie viele davon ganz ohne behaltenen
Eintrag, wie viele behaltene Einträge insgesamt, Selbstcheck bestanden ja oder nein.
