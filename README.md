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
Fehler Fallback auf den direkten SQLite/FTS5-Pfad. Schlug schon `/classify`
fehl, gilt der Daemon für diesen Lauf als down und `/search` wird gar nicht
erst versucht (Windows brennt für connection-refused auf localhost den vollen
Timeout ab, gemessen ~0.5s pro totem Call).

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

## Telemetrie
`prompt_prelude.jsonl` (gitignored, bleibt lokal): pro Prompt ein Event mit
skip-Grund ODER `fired`-Routing. Auditierbare Felder pro Event:
- `v` (Schema-Version, aktuell 4 = stdin-UTF-8-Fix): v1-v3-Events sind
  Mojibake-vergiftet (cp1252-stdin) — Routing-/Compliance-Auswertungen und
  Threshold-Kalibrierung NUR innerhalb v4 fahren, nie über Versionen mischen,
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
