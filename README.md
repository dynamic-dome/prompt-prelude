# prompt-prelude

UserPromptSubmit-Hook: routet Claude domänen-gezielt ins Capability-RAG (M2-Discovery),
mit sichtbarer `↳ prelude`-Quittung, Telemetrie (`prompt_prelude.jsonl`) und Session-Dedupe.

## Verhalten
- **Opt-out:** Prompt mit `//raw` beginnen → Hook überspringt (case-insensitiv).
- **Still bei:** trivialen/kurzen Prompts, ohne erkannte Domain/Planung, oder wenn
  `domain:phase` in dieser Session schon geroutet wurde (Dedupe). Der Key ist
  bewusst `domain+phase`: quiet→planning derselben Domain feuert erneut.
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
- **HARD-Regeln (§3 Test-DB-Isolation etc.)** liegen bewusst NICHT hier, sondern im
  PreToolUse-Block-Hook (enforcing), nicht in diesem advisory-Kanal.

## Telemetrie
`prompt_prelude.jsonl` (gitignored, bleibt lokal): pro Prompt ein Event mit
skip-Grund ODER `fired`-Routing. Auditierbare Felder pro Event:
- `prompt_preview` (erste 80 Zeichen) auf allen Events,
- bei `fired`: `matched_keywords` (Domain- + Planning-Treffer), `caps`,
  `caps_count` (0 = toter BM25-Lookup, fällt sofort auf), `rearmed`,
- `skip: "bad_stdin"` bei abgeschnittenem/invalidem stdin-JSON,
- `skip: "crash"` + `error` (best-effort) wenn `run()` wirft.
Auswerten, um tote Routings und Domänen-Lücken zu finden.

## Housekeeping
`.dedupe/`-Dateien älter als 7 Tage werden bei jedem Lauf fail-soft gelöscht.

## Tests
```
python -m pytest test_prompt_prelude.py -q
```

## Registrierung
Als erstes `UserPromptSubmit`-Matcher-Objekt in `~/.claude/settings.json`, mit
explizitem `"timeout": 2` (gegen den 30s-Default-Hänger). Reine-stdlib, kein pip.
