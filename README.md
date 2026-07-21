# prompt-prelude

UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG (M2-Discovery),
mit sichtbarer `↳ prelude`-Quittung, Telemetrie (`prompt_prelude.jsonl`) und Session-Dedupe.

## Verhalten
- **Opt-out:** Prompt mit `//raw` beginnen → Hook überspringt (case-insensitiv).
- **Still bei:** trivialen/kurzen Prompts, ohne erkannte Domain/Planung, oder wenn
  `domain:phase` in dieser Session schon geroutet wurde (Dedupe). Der Key ist
  bewusst `domain+phase`: quiet→planning derselben Domain feuert erneut.
- **Maschinen-Prompts:** beginnt der Prompt mit `<task-notification>`,
  `<system-reminder>`, `<local-command-stdout>` oder `<command-name>`
  (harness-generiert, kein User-Intent), wird mit `skip: "machine_prompt"`
  übersprungen. Live-Befund 2026-07-02: Subagent-Callbacks produzierten
  Fehl-Routings (ui-frontend auf Telemetrie-Reports) und verzerrten die
  H4-Compliance-Messung.
- **Re-Arm:** Prompts mit explizitem RAG-/Skill-Bezug ("welche skills",
  "memory_search", "capability", "fähigkeiten", …) feuern trotz Dedupe erneut.
- **Keyword-Matching:** Wortgrenzen (`\b`), kein Substring — `ui` matcht nicht mehr
  "build"/"guide"/"quiet". Keywords mit trailing `*` sind Präfix-Stems
  (`implementier*` → "implementieren").

## ECHO-Zeile (Default: aus)
Die erzwungene erste Antwortzeile `↳ prelude · [phase] [domain] · RAG-Auftrag aktiv`
war Rollout-Verifikation und verunreinigt dauerhaft Antworten. Sie wird nur noch
emittiert, wenn die Env-Variable `PRELUDE_ECHO=1` gesetzt ist (jeder andere Wert
oder unset = aus). Zum Verifizieren eines neuen Rollouts temporär setzen, danach
wieder entfernen.

## Mechanismus
- **M2 (Kern):** domänen-gezielte RAG-Aufträge an Claude (`build_rag_routing`).
- **BM25 (optional, fail-soft):** Treffer-Hinweise aus dem lokalen Atlas-Index.
- **Wording (H1, seit 2026-07-02 abend):** keine Selbst-Entwertung mehr — die
  früheren Labels "weicher Hinweis, kein Befehl" und "optional" gaben dem Modell
  explizite Erlaubnis wegzuschauen (H4: 3-4 % Compliance ≈ Baseline). Jetzt:
  imperativer Auftrag ("vor dem ersten Arbeitsschritt erledigen"), Caps als
  **vorgezogenes Suchergebnis** ("bereits ausgeführt — prüfe diese Treffer
  zuerst": lesen statt selbst suchen) plus fertige
  `memory_search_tool("<query>")`-Zeile zum Vertiefen. Der Funnel (Dedupe,
  Phasen, Skips) deckelt die Frequenz weiterhin — Cry-Wolf-Schutz liegt dort,
  nicht in der Wortwahl.
- **HARD-Regeln (§3 Test-DB-Isolation etc.)** liegen bewusst NICHT hier, sondern im
  PreToolUse-Block-Hook (enforcing), nicht in diesem advisory-Kanal.

## Semantisches Routing (Atlas-Daemon)
Die Domain-Erkennung ist eine **Kaskade**, keyword-Matching bleibt vollständig erhalten:

1. **Daemon-Klassifikation:** `POST /classify` an den Atlas-HTTP-Daemon
   (Embedding-Cosine gegen die 6 `DOMAIN_DESCRIPTIONS`-Anker plus die
   `NULL_ANCHORS`, Prompt auf 500 Zeichen gekappt). Akzeptiert nur bei
   `score >= TH_ACCEPT` (0.45, kalibriert 2026-07-02 via eval_routing.py) **und**
   (Margin zum Zweitplatzierten `>= TH_MARGIN` (0.05) **oder** `score >= TH_CLEAR`
   (0.50)). Unbekannte Label-Namen werden abgelehnt. **Null-Anker:** gewinnt der
   "meta-none"-Anker oder liegt er naeher als `TH_ANCHOR_VETO` (0.12) am Sieger
   (alle Plaetze werden gescannt), gilt der Prompt als meta/unsicher -> Fallback.
2. **Keyword-Fallback:** bei Daemon-Fehler/Timeout/Non-200/Threshold-Ablehnung
   greift das bestehende Wortgrenzen-Matching (`DOMAIN_HINTS`) unverändert.
3. **Phase (planning/quiet)** bleibt bewusst rein Keyword-basiert.

Der **Caps-Lookup** kaskadiert analog: `POST /search` zuerst (Hints werden mit
`heading`/`snippet` informativer: `record_id — Titel`, 60-Zeichen-Cap), bei
Fehler Fallback auf den direkten SQLite/FTS5-Pfad.

**Caps-Gating (v5, 2026-07-07):** injiziert werden nur noch Capability-Records
(record_id-Präfix `atlas/`), mit k=12 überholt und client-seitig gefiltert
(SQLite analog via `LIKE 'atlas/%'`). Hintergrund: die `/search`-Scores sind
RRF-Rang-Fusion (~0.014–0.023 für gute wie Müll-Queries) — ein Score-Threshold
kann NICHT als Relevanz-Gate dienen. Live-Probe: gute Capability-Queries haben
1–2 `atlas/`-Treffer in den Top-10, Junk-Queries exakt 0 — der Präfix-Filter
ist damit Scope-Korrektur und Relevanz-Gate zugleich; leere Caps sind gewollt
besser als falsche (der VORAB-SUCHE-Block entfällt dann, der RAG-Auftrag
bleibt). Headings aus reinen Strukturzeichen ("---") fallen auf Snippet/
record_id zurück. `extract_query` stellt keine Domain-Labels mehr voran
(Label-Namen sind keine Suchbegriffe; Content-Wörter wie "frontend" bleiben)
und filtert Live-beobachtete Füllwörter; unter 2 Content-Tokens entfällt die
Vertiefungszeile. Schlug schon `/classify`
fehl, gilt der Daemon für diesen Lauf als down und `/search` wird gar nicht
erst versucht (Windows brennt für connection-refused auf localhost den vollen
Timeout ab, gemessen ~0.5s pro totem Call).

**Ghost-Mentor (v7, 2026-07-19):** zweite Vorab-Suche-Partition "Frühere Fälle"
aus DENSELBEN /search-Overfetch-Ergebnissen (kein zusätzlicher Daemon-Call,
kein Budget-Impact). Injiziert werden ähnlich gelöste Fälle aus der
Präfix-Allowlist `haupt-wiki/queries/` (Session-Notes), `summary-harvest/`
(geerntete Summaries) und `agent-memory/` (Decisions/Learnings), max. 2
(`MENTOR_LIMIT`). Weil wiki-Treffer auch auf Junk-Queries existieren (v5-Befund:
RRF-Scores gaten nicht), gilt zusätzlich ein Token-Overlap-Gate: ein Hint muss
min. 2 signifikante Query-Tokens (>=4 Zeichen) tragen — leer ist gewollt besser
als falsch. SQLite-Fallback analog (`_query_mentor_sqlite`, nur record_ids).
Die sichtbare Statuszeile trägt `· mentor=N` nur bei Treffern (Format sonst
unverändert).

**Budget-Guard:** alle Daemon-Calls eines Laufs teilen sich ~1.2s
(`DAEMON_BUDGET_S`); ist das Budget verbraucht, werden weitere Daemon-Calls
geskippt und die Fallbacks greifen. Jeder einzelne Call hat einen kleinen
Timeout (Default 0.5s). Der Hook blockiert nie.

**Env-Vars:**
- `ATLAS_DAEMON_URL` — Daemon-Basis-URL (Default `http://127.0.0.1:7801`)
- `ATLAS_DAEMON_TIMEOUT` — Timeout pro Call in Sekunden (Default `0.5`)

**A/B-Telemetrie:** Daemon- UND Keyword-Ergebnis werden immer geloggt
(`routing_source` = `daemon|keywords|none`, `daemon_top` = Top-3 name+score,
`keyword_domain`, `daemon_latency_ms`, `caps_source` = `daemon|sqlite|none`).

**Kalibrierung:** `python eval_routing.py` läuft manuell gegen den echten
Daemon (~20 eingebettete DE/EN-Prompts inkl. bekannter Fehlklassifikations-Fälle)
und stellt daemon- vs. keyword-Domain als Tabelle gegenüber. Die drei
Thresholds (`TH_ACCEPT`, `TH_MARGIN`, `TH_CLEAR`, `TH_ANCHOR_VETO`) sind
Kalibrierungs-Kandidaten — nach Datenlage nachziehen, zusätzlich
`prompt_prelude.jsonl` auswerten. Stand 2026-07-02: 18/20 Eval-Prompts korrekt,
0 False-Positives; die 2 Restfehler sind englische Prompts, bei denen beide
Schichten blind sind (cross-linguale MiniLM-Schwaeche).

## stdin-Encoding (v4, 2026-07-06)
stdin wird über `sys.stdin.buffer` (Bytes) gelesen und explizit als UTF-8
dekodiert. Vorher dekodierte der Text-Stream auf Windows als cp1252 — **jeder**
Umlaut kam als Mojibake an (0/208 v3-Events korrekt), Umlaut-Keywords matchten
nie, die Daemon-Klassifikation lief auf Müll-Text (1/123 daemon-Routings live
vs. 18/20 in der In-Process-Eval). Regression wird durch einen echten
Subprocess-E2E-Test gefangen (`TestStdinEncodingE2E`) — In-Process-stdin-Mocks
können diese Bug-Klasse prinzipiell nicht sehen.

## Skill-Routing (v8, 2026-07-22)
Zweiter Kanal neben dem RAG-Auftrag: statt Material zu liefern, nennt er die
**auszuführende Aktion** — mit fertigem `Skill("name")`-Aufruf und, wo es eine
echte Verwechslung gibt, mit explizitem **NICHT** samt Begründung. Das
Negativ-Routing ist der eigentliche Wert: es kodiert Abgrenzungen, die sonst
nirgends stehen (z.B. dass inhaltliche Code-Reviews über Codex laufen).

Warum ein eigener Kanal: Der RAG-Auftrag injiziert die Caps **fertig mit**, der
Agent kann sie passiv konsumieren — deshalb misst `eval_compliance` dort nur
15 % → 18 % und kann "ignoriert" nicht von "schon geliefert" trennen (Befund 7).
Ein SKILL.md-Body lässt sich nicht vorab injizieren: der Agent ruft ihn auf oder
nicht. Der Kanal ist damit härter **und** sauber messbar.

Konfiguration in `prompt_prelude.py`:
- `SKILL_RULES` — keyword-getriggert, domänenunabhängig, höchste Priorität
- `SKILL_ROUTING` — pro Domain
- `SKILL_PHASE_ROUTING` — pro Phase (aktuell nur `planning`)
- `SKILL_HINT_MAX = 2` — Deckel, sonst kippt der Block von Wegweiser zu Wand

**Scope-Regel:** geroutet wird nur, wo mehrere Skills um denselben Anlass
konkurrieren oder eine harte CLAUDE.md-Regel einen Skill verlangt. Projekt-
gebundene Skills (`ich-mentor`, `ich-loop`, `stackatlas-content-studio`,
`website-check`) bleiben bewusst draußen — dort ist die Wahl eindeutig, ein
Hinweis wäre Rauschen. Tote Skills gehören ins Archiv, nicht ins Routing.

**Wirksamkeit messen:** `python eval_skill_routing.py` joint die Telemetrie mit
den Claude-Code-Transkripten (`~/.claude/projects/**.jsonl`) und prüft, ob ein
empfohlener Skill danach wirklich gerufen wurde — als Skill-Tool **oder** als
getippter Slash-Command. Beide Quellen zählen; wer nur `"skill":"…"` zählt,
hält benutzte Skills fälschlich für tot (Messfehler vom 2026-07-22).
**Baseline vor Einführung: 37/389 = 10 %** der fired-Events zogen einen der
routbaren Skills von selbst. Das ist die Messlatte.

Ein Guard-Test (`TestNoDeadSkillReferences`) verhindert, dass das Routing
Skills bewirbt, die es nicht mehr gibt — Anlass war `diagnose-hitl` (liegt in
`~/.claude/skills/_archive/`) und `modern-web-design` (Plugin auf `false`), die
beide monatelang in `DOMAIN_ROUTING` standen. **Bei Skill-Aufräumrunden hier
mitziehen.**

## Telemetrie
`prompt_prelude.jsonl` (gitignored, bleibt lokal): pro Prompt ein Event mit
skip-Grund ODER `fired`-Routing. Auditierbare Felder pro Event:
- `v` (Schema-Version, aktuell 8 = Skill-Routing, neue Felder
  `skill_hint`/`skill_hint_count` bei `fired`; 7 = Ghost-Mentor-Partition, Felder
  `mentor`/`mentor_count`/`mentor_source` + geänderte Injektions-Semantik;
  v6 = Threshold-Kalibrierung T-8; v5 = Caps-Gating atlas/-only + Query-Cleanup;
  v4 = stdin-UTF-8-Fix): v1-v3-Events sind Mojibake-vergiftet (cp1252-stdin),
  v4 hat andere Caps-Semantik als v5 — Routing-/Compliance-Auswertungen und
  Threshold-Kalibrierung NUR innerhalb einer Version fahren, nie mischen,
- bei `fired` (v7): `mentor` (injizierte Frühere-Fälle-Hints), `mentor_count`,
  `mentor_source` (`daemon|sqlite|none`),
- bei `fired`: `caps_raw_count` (Treffer VOR dem atlas/-Filter) neben
  `caps_count` — zeigt, wie viel das Gate wegschneidet,
- `prompt_preview` (erste 80 Zeichen) auf allen Events,
- bei `fired`: `matched_keywords` (Domain- + Planning-Treffer), `caps`,
  `caps_count` (0 = toter BM25-Lookup, fällt sofort auf), `rearmed`,
  `query` (die in den Kontext eingebettete memory_search-Query),
- `skip: "bad_stdin"` bei abgeschnittenem/invalidem stdin-JSON,
- `skip: "crash"` + `error` (best-effort) wenn `run()` wirft.
Auswerten, um tote Routings und Domänen-Lücken zu finden.

**Compliance-Beweis (H4):** `python eval_compliance.py` joint die Telemetrie
mit den tool-usage-tracker-Events (`../tool-usage-tracker/data/events*.jsonl`):
folgt auf ein `fired`-Event tatsächlich ein Atlas-Read-Call derselben Session
im 15-Min-Fenster (Konsum-Join, ein Call zählt für höchstens ein Event)?
Plus Skip-Baseline (Calls trotz unterdrückter Prelude). Ersetzt die
ECHO-Quittung durch Ground-Truth. **Erstbefund 2026-07-02: 1/26 fired-Events
befolgt (4 %), Skip-Baseline 3 % — der Hinweis ändert das Agent-Verhalten
bisher praktisch nicht.** Kandidaten: Prelude-Wording schärfen (imperativer),
Fenster/Attribution prüfen, nach H1-Telemetriewoche neu messen.

## Housekeeping
`.dedupe/`-Dateien älter als 7 Tage werden bei jedem Lauf fail-soft gelöscht.

## Tests
```
python -m pytest test_prompt_prelude.py -q
```

## Registrierung
Als erstes `UserPromptSubmit`-Matcher-Objekt in `~/.claude/settings.json`, mit
explizitem `"timeout": 2` (gegen den 30s-Default-Hänger). Reine-stdlib, kein pip.

## Trajektor (PostToolUse-Schwester, T-12)

`trajektor.py` beobachtet den Tool-Call-Strom und misst Drift gegen den letzten
Arbeits-Prompt (Goal-Anchor, geschrieben von prompt_prelude beim Gate-Pass).
Deterministischer 3-Komponenten-Score (token_shift 0.5 / path_divergence 0.3 /
phase_flip 0.2), Hysterese fire=0.65/clear=0.45, Cooldown 10 Calls, max. 3
Fires/Session. Bei Fire: Reframing-Zeile als additionalContext + sichtbare
systemMessage. Fail-soft, Exit immer 0, keine Daemon-Calls.

Telemetrie: `trajektor.jsonl`, Ära **t1** (`tv`-Feld) — nie mit
`prompt_prelude.jsonl`-Ären mischen. Kalibrierung der Schwellen ist bewusst
nachgelagert (Telemetrie-Auswertung analog T-11).
