<!-- bridge:begin — generiert von agentic-os bridge_projection, NICHT von Hand editieren -->
## Bridge: Offene Tasks (membrain)
- [T-7] Tunable pruefen: Meta-/Continue-Prompts ('mach weiter mit dem plan') feuern jetzt auch general — RAG hilft da wenig. Falls zu laut, Meta-Skip (NULL_ANCHOR-Signal) vor general-Fallback ziehen. — Teila…
- [T-2] Dedupe-Re-Arm: durch v3 topic-signature-Dedupe teilweise adressiert (neues Thema feuert wieder). Offen nur noch falls Zeit-basiertes Re-Arm gewuenscht.
- [T-3] Englische Prompts: Keyword-Schicht + DOMAIN_DESCRIPTIONS iterieren (2/20-Eval-Luecke) — durch general-Fallback jetzt weniger dringend (EN-Prompts feuern wenigstens general).
- [T-4] Architekturfrage advisory vs. PreToolUse-Gate — Eval untermauert Schwaeche des advisory-Kanals, aber Owner-Entscheid ist der weiche v3-Pivot (sichtbar+auto-konsultieren). Gate bleibt zurueckgestellt.
- [T-9] E2E-Test hermetisieren (Codex-Verifier-Nit, Low): TestStdinEncodingE2E liest nach totem Daemon read-only den echten BM25-Index (ATLAS_ROOT_DEFAULT nicht env-überschreibbar). Optional Env-Override ein…
(3 weitere: context/open-tasks.json)
## Bridge: Learnings von Claude (kuratiert)
- [L13] (2026-07-20) Vier Claude-Reviews (task-scoped + Opus-Whole-Branch) übersahen 3 Medium-Bugs in einer zustandsbehafteten Drift-Heuristik, die der Codex-Verifier mit EIGENEN Repro-Skripten fand (False-Fire bei Re-Anchor 0.83, Substring-Pfad-Match, verlorene Drive-Letter). Diff-lesende Reviews prüfen Code-Gestalt, nicht Zustands-Trajektorien — Verifier für State-Maschinen explizit mit Repro-/Ausführungs-Pflicht briefen (ergänzt P13/DCO).
<!-- bridge:end -->
