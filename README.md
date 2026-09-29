# prompt-prelude

A `UserPromptSubmit` hook for Claude Code. Before the agent reads my prompt, the hook searches my own
knowledge index for matching material (skills and tools that fit the task, notes from earlier sessions
that solved something similar) and adds the best hits as a short block of context. If it finds nothing
concrete, it stays silent.

## Why

I work with a large set of skills, plugins and MCP tools and a wiki that keeps growing. The agent does
not look things up on its own at the right moment, so the hook does the lookup before the agent starts.
In an end-to-end check, a session start took 359 ms including Python start-up.

## What the measurements say

Each change is checked against a threshold written down before the measurement
(`NOTES-live-findings.md`):

- **Telling the agent what to do barely changed what it did.** In v8 the hook recommended a skill.
  Over two weeks of live data the agent followed in 11 % of cases, against 6 % without the hint:
  +5 percentage points, below the bar set in advance. Since v9 the hook injects results only, never
  instructions, and fires only when it has material.
- **The first prompt of a session is where it helps.** v11 adds up to two hits for the current project
  on the first prompt. On 46 untouched session starts, helpful hits rose from 9 % to 26 % and pure
  noise fell from 26 % to 20 %.
- **Most injected material goes unused.** Over 30 days, 4 of 227 injected hits were demonstrably used
  (1.8 %, a lower bound). v12 therefore stops injecting capability hits in continued sessions.

The measurements replay real prompts against variants of the hook and have the results rated by a
separate model that does not know which variant it is looking at (`replay/`).

## Limits

- It depends on my private search index (agent-memory-atlas: a local daemon, with a SQLite BM25 file
  as fallback). Without it the hook finds nothing and stays silent. It never blocks a prompt: every
  step is fail-soft and the hook always exits 0.
- The index is expected under `~/AI/agent-memory-atlas/.atlas-index`; set `PRELUDE_ATLAS_ROOT` to
  point elsewhere, and `ATLAS_DAEMON_URL` for the daemon.
- Built and used on Windows 11, Python standard library only.
- The notes below and the measurement log `NOTES-live-findings.md` are in German.

Tests: `python -m pytest -q`. `conftest.py` keeps every test away from the real logs and the real daemon.

License: MIT

---

## Technical notes (German)

UserPromptSubmit-Hook, seit v9 (Advisory-Pivot, 2026-08-04) ein **Material-Kanal**:
injiziert vorab gesuchte Capability-Treffer, frühere Fälle und (selten) einen
Skill-Hint — und schweigt, wenn es nichts Konkretes zu liefern gibt.
Telemetrie (`prompt_prelude.jsonl`), Decision-Log, Session-Dedupe.

## v9-Leitidee: kein Material → kein Feuer
Die v8-Messung (NOTES Befund 10) riss die vorregistrierte Falsifikationsschwelle
des Advisory-Kanals: imperative Auftragstexte änderten das Agent-Verhalten nicht
(+5 pp), 52 % der Feuerungen trugen nur generischen Text (`caps_count=0`).
Konsequenz: die domänenspezifischen RAG-Auftragstexte (`DOMAIN_ROUTING`) sind
entfernt; gefeuert wird nur noch, wenn Caps, Mentoren oder ein Skill-Hint
vorliegen — sonst `skip: "no_material"` (Telemetrie trägt `caps_source`/
`mentor_source`, um "nichts gefunden" von "Quelle down" zu trennen).
Verhaltens-Hinweise, deren Moment NICHT der Prompt-Zeitpunkt ist, ziehen in
Phase 2 an ihre Lifecycle-Punkte um: `docs/2026-08-04-vorgehen-advisory-pivot.md`.

## Verhalten
- **Opt-out:** Prompt mit `//raw` beginnen → Hook überspringt (case-insensitiv).
- **Still bei:** trivialen/kurzen Prompts, ohne Work-Signal (seit v9 zählt
  `planning` als Work-Signal, Befund 9/10), ohne Material (`no_material`),
  oder wenn `domain:phase` in dieser Session schon geroutet wurde (Dedupe;
  ein `no_material`-Skip verbrennt den Dedupe-Key NICHT). Der Key ist
  bewusst `domain+phase`: quiet→planning derselben Domain feuert erneut.
- **Maschinen-Prompts:** beginnt der Prompt mit `<task-notification>`,
  `<system-reminder>`, `<local-command-stdout>`, `<command-name>` oder
  (seit v10) `<cross-session-message` bzw. `<agent-message` (harness-generiert,
  Nachricht einer anderen Claude-Session oder Subagent-Hand-back, kein
  User-Intent), wird mit `skip: "machine_prompt"`
  übersprungen. Live-Befund 2026-07-02: Subagent-Callbacks produzierten
  Fehl-Routings (ui-frontend auf Telemetrie-Reports) und verzerrten die
  H4-Compliance-Messung. `<pasted_content>` bleibt bewusst User-Intent.
- **Session-Start-Projektkarte (v11):** Beim ersten Prompt einer Session (das
  Transkript enthält noch keine Assistant-Antwort) wird ab 40 Zeichen auch
  ohne Work-Signal gesucht, und zusätzlich zur normalen Suche kommen bis zu
  2 Treffer des aktuellen Projekts (Slug aus `cwd`, zweite `/search` mit
  Projektname) **mit Inhalt** in den Block `PROJEKT-KONTEXT <slug>`.
  Validiert an 46 unberührten Session-Anfängen (Befund 14): hilfreiche
  Prompts 9 % → 26 %, reines Rauschen 26 % → 20 %. Bei Fortsetzungen bleibt
  alles wie v10 (dort brachte die Projekt-Partition nur +5 pp).
- **Keine Caps bei Fortsetzungen (v12):** Zeigt das Transkript schon eine
  Assistant-Antwort, werden `atlas/`-Caps nicht eingespielt (Befund 15: 18 %
  Präzision, ~1 % Nutzung; explorativ noise 24 % → 4 %). Mentoren, Skill-Hint
  und Session-Start bleiben; bei unbekanntem Session-Zustand alles wie v11.
- **Headless-Läufe (v10):** `CLAUDE_CODE_ENTRYPOINT` beginnt mit `sdk`
  (`claude -p` = `sdk-cli`, Agent-SDK = `sdk-py`/`sdk-ts`) → `skip: "headless"`.
  Befund 12: v9 injizierte 37× Material in DCO-Headless-Digests. Fehlt die
  Variable, gilt der Lauf als interaktiv. Jedes Telemetrie-Event trägt
  `entrypoint`, sofern gesetzt.
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

## Mechanismus (v9)
- **Material (Kern):** Caps als **vorgezogenes Suchergebnis** ("bereits
  ausgeführt — prüfe diese Treffer": lesen statt selbst suchen) plus fertige
  `memory_search_tool("<query>")`-Zeile zum Vertiefen (ab 2 Content-Tokens);
  dazu die Ghost-Mentor-Partition (frühere Fälle). Domänenspezifische
  Auftragstexte gibt es nicht mehr (Befund 10); bei `phase=planning` fährt
  eine einzelne Politik-Zeile (SE-Wissensbasis §13 / Sparring §19) huckepack.
- **BM25 (optional, fail-soft):** Treffer-Hinweise aus dem lokalen Atlas-Index.
- **Funnel:** Dedupe, Phasen, Skips und das Material-Gate deckeln die Frequenz —
  Cry-Wolf-Schutz liegt dort, nicht in der Wortwahl.
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

## Skill-Routing (v8 → v9 reduziert)
v8 baute den Kanal als messbaren Advisory-Test; die Auswertung nach 2 Wochen
(Befund 10) zeigte: **nur Skills, deren Handlungsmoment der Prompt-Zeitpunkt
ist, werden befolgt** (subagent-briefing 12 %, systematic-debugging 12 % —
einzige über der 6 %-Baseline). Alle anderen lagen bei 0–2 %, weil ihr Moment
später liegt (Subagent-Report trifft ein, Review am Task-Ende, …).

v10-Bestand: nur noch `SKILL_ROUTING` für `workflow` (subagent-briefing).
`debug` (systematic-debugging) ist seit v10 raus — er riss die vorregistrierte
v9-Latte (0/8 Follow, v8 1/8; NOTES Befund 12). subagent-briefing besteht
formal (v9 2/33 vs. 1 % Baseline), aber auf n=2 — nach dem Automaten-Filter
neu messen. `SKILL_RULES` und `SKILL_PHASE_ROUTING` sind
leer — sqlite-schema-guard, review, verify-subagent-tallies & Co. ziehen in
Phase 2 an ihre Lifecycle-Punkte (PostToolUse/PreToolUse/Stop) um, siehe
`docs/2026-08-04-vorgehen-advisory-pivot.md`. `SKILL_HINT_MAX = 2` bleibt.
Ein Skill-Hint zählt als Material (feuert auch ohne Caps).

**Scope-Regel unverändert:** geroutet wird nur, wo mehrere Skills um denselben
Anlass konkurrieren. Tote Skills gehören ins Archiv, nicht ins Routing
(Guard-Test `TestNoDeadSkillReferences`).

**Wirksamkeit messen:** `python eval_skill_routing.py` (Default jetzt
`--min-version 9`) joint die Telemetrie mit den Claude-Code-Transkripten und
zählt Skill-Tool **und** getippte Slash-Commands (Default seit v10
`--min-version 10`). **Vorregistrierte v9-Latte:** die zwei verbliebenen Skills
müssen ≥ 2× Baseline halten, sonst fliegen auch sie (Abschalt-Kriterium im
Plan-Dokument) — angewandt in Befund 12. **Achtung:** Claude Code löscht
Transkripte nach `cleanupPeriodDays` (Default 30) — die Eval sieht nur, was
noch da ist. Historischer v8-Stand: FOLLOW 11 %
vs. Baseline 6 % über alle 8 damals routbaren Skills.

Ein Guard-Test (`TestNoDeadSkillReferences`) verhindert, dass das Routing
Skills bewirbt, die es nicht mehr gibt — Anlass war `diagnose-hitl` (liegt in
`~/.claude/skills/_archive/`) und `modern-web-design` (Plugin auf `false`), die
beide monatelang in `DOMAIN_ROUTING` standen. **Bei Skill-Aufräumrunden hier
mitziehen.**

## Telemetrie
Zwei Log-Dateien (beide gitignored, bleiben lokal):

`prelude_decisions.jsonl`: pro Prompt ein Decision-Record des Precision-Gates
(`decision: emit|skip`, `reason`, Klassifikation mit `confidence`/`daemon_top`/
`matched_keywords`, `work_signals`). Das ist die Debug-Sicht: WARUM hat der
Hook (nicht) gefeuert. Auswerten, um Gate-Fehlentscheidungen zu finden
(z. B. planning-Prompts, die an `no_work_signal` scheitern — Befund 9/10).

`prompt_prelude.jsonl` (Haupt-Telemetrie): pro Prompt ein Event mit
skip-Grund ODER `fired`-Routing. Auditierbare Felder pro Event:
- `v` (Schema-Version, aktuell 12 = Caps aus bei Fortsetzung: Feld
  `caps_suppressed`, `caps_source="suppressed"` — nicht mit v11 mischen;
  11 = Session-Start-Projektkarte: Feld
  `session_start` auf post-classify-Events, `project`/`project_count`/
  `project_source`/`project_slugs` bei fired und no_material — mehr
  Feuerungen am Session-Anfang, nicht mit v10 mischen;
  10 = Automaten-Filter: Skip `headless`,
  `<cross-session-message` als machine_prompt, Debug-Skill-Zeile raus, Feld
  `entrypoint` — fired-Population ohne Automaten, nicht mit v9 mischen;
  9 = Advisory-Pivot: neuer Skip `no_material`
  mit `caps_source`/`mentor_source`/`query`/`caps_raw_count` am Skip-Event,
  kein Leer-Feuern mehr — fired-Raten haben einen ANDEREN Nenner als v8;
  8 = Skill-Routing, neue Felder
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

## Replay-Harness + Relevanz-Judge (seit 2026-09-28)
Offline-Messung der Material-Qualität, statt jede Änderung als 2-Wochen-Live-Ära
zu fahren. Plan + vorregistriertes Übernahmekriterium:
`docs/2026-09-28-plan-relevanz-replay.md`.

```
python -m replay.corpus            # Korpus aus Transkripten fortschreiben (append-only)
python -m replay.run               # Varianten V0-V3 gegen den Daemon fahren
python -m replay.judge prepare     # neue (Prompt, Record)-Paare als Batches
#   -> je Batch EIN Sonnet-Subagent mit replay/JUDGE_BRIEF.md, schreibt verdicts_NN.jsonl
python -m replay.judge score       # Metriken je Variante + Kriterium
python -m replay.uptake            # Nutzung: greift Claude eingespielte Treffer auf? (Befund 15)
```

- **Korpus:** echte, getippte Prompts interaktiver Sessions in Hook-Sicht
  (Slash-Commands als `/name args`, Bash-Modus raus), mit `cwd` und letzter
  Assistant-Antwort. Liegt in `replay/data/` — **gitignored** (volle Prompts,
  Repo ist öffentlich). Append-only, damit `cleanupPeriodDays` nichts löscht:
  regelmäßig `python -m replay.corpus` laufen lassen.
- **Gate-Nachbau validiert:** 836/836 Korpus-Prompts stimmen mit der
  v9-Live-Telemetrie überein.
- **Judge:** blind (keine Varianten-/Partitions-Info), Paar-Cache, Noten 0/1/2.
  Kalibrierung Batch 1: 13/15 Übereinstimmung mit Agent-Urteil bei "2 vs.
  nicht 2" — tendenziell etwas großzügig, für Varianten-Vergleiche tauglich.

## Daemon-Keepalive (Idee 8, seit 2026-09-28)
`tools/daemon_keepalive.py` schickt alle 5 Minuten ein Mini-`/search` an den
Atlas-Daemon (User-Task `Prelude-Atlas-Keepalive`, pythonw, nur bei Anmeldung).
Anlass Befund 12: 21 % der `/classify`-Calls liefen ins Timeout, nach ≥ 1 h
Leerlauf 59 %. Jeder Ping wird mit Latenz nach `keepalive.jsonl` geloggt
(gitignored). Registrieren/Entfernen:
`powershell -ExecutionPolicy Bypass -File tools\register-keepalive-task.ps1 [-Unregister]`.
Erfolgskriterium: `daemon_latency_ms >= 450` sinkt in der Telemetrie unter 8 %.

## Housekeeping
`.dedupe/`-Dateien älter als 7 Tage werden bei jedem Lauf fail-soft gelöscht.

## Tests
```
python -m pytest test_prompt_prelude.py -q
```

## Registrierung
Als erstes `UserPromptSubmit`-Matcher-Objekt in `~/.claude/settings.json`, mit
explizitem `"timeout": 2` (gegen den 30s-Default-Hänger). Reine-stdlib, kein pip.

## Trajektor (PostToolUse-Schwester, T-12) — ARCHIVIERT 2026-08-04

`trajektor.py` beobachtete den Tool-Call-Strom und maß Drift gegen den letzten
Arbeits-Prompt (Goal-Anchor, geschrieben von prompt_prelude beim Gate-Pass).
Deterministischer 3-Komponenten-Score (token_shift 0.5 / path_divergence 0.3 /
phase_flip 0.2), Hysterese fire=0.65/clear=0.45, Cooldown 10 Calls, max. 3
Fires/Session.

**Status: offiziell beerdigt (Owner-Entscheid 2026-08-04, NOTES Befund 11).**
Der Hook war seit ~2026-07-28 still aus `settings.json` deregistriert;
die Telemetrie (26.128 Events, 20.–28.07.) zeigt 37 Fires und **62 %
`no_anchor`** — weil der Goal-Anchor nur beim seltenen Prelude-Gate-Pass
geschrieben wurde, lief der Trajektor strukturell meist blind. Code + Tests
bleiben im Repo (Referenz-Implementierung einer deterministischen
Drift-Heuristik), werden aber nicht weiter gepflegt. Eine Wiederbelebung
bräuchte zuerst die Anchor-Entkopplung von der Prelude-Fire-Rate.

Telemetrie: `trajektor.jsonl`, Ära **t1** (`tv`-Feld), eingefroren.
