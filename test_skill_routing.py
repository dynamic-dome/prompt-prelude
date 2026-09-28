# -*- coding: utf-8 -*-
"""v8/v9/v10 — Skill-Routing.

v8 (2026-07-22) baute den Kanal als messbaren Advisory-Test. v9 (2026-08-04,
NOTES Befund 10) zog die Konsequenz aus der Messung: es bleiben nur die zwei
Skills, deren Handlungsmoment der Prompt-Zeitpunkt ist (subagent-briefing,
superpowers:systematic-debugging — je 12 % Follow, einzige über Baseline).
v10 (2026-09-28, NOTES Befund 12): systematic-debugging riss die v9-Latte
(0/8) und ist raus; es bleibt nur subagent-briefing.
SKILL_RULES und SKILL_PHASE_ROUTING sind leer; ihre Regeln ziehen in Phase 2
an die richtigen Lifecycle-Punkte um (PostToolUse/PreToolUse). Diese Tests
sichern den v9-Kontrakt: reduzierter Bestand, Deckel, Telemetrie, Guard.
"""
import json as _json

import prompt_prelude as pp

DEBUG_PROMPT = ("der parser wirft einen fehler beim einlesen, "
                "bitte debugge das in src/parser.py")
WORKFLOW_PROMPT = ("baue einen workflow mit zwei subagenten, "
                   "die den nightly cron job überwachen")


class TestBuildSkillRouting:
    def test_empty_without_match(self):
        assert pp.build_skill_routing("data-analysis", "quiet",
                                      "mach eine tabelle daraus bitte") == []

    def test_v9_rules_are_empty(self):
        # v9: pytest-/review-Trigger ziehen NICHT mehr am Prompt-Zeitpunkt —
        # sqlite-schema-guard (1/12) und review (1/55) waren praktisch tot und
        # ziehen in Phase 2 an PreToolUse/Stop um.
        assert pp.SKILL_RULES == ()
        assert pp.build_skill_routing("general", "quiet",
                                      "laeuft pytest hier gegen eine echte db?") == []
        assert pp.build_skill_routing("general", "quiet",
                                      "kannst du das nochmal reviewen bitte") == []

    def test_v9_no_planning_phase_routing(self):
        # office-hours/brainstorming/plan-ceo-review: 0-8 % Follow -> raus.
        assert pp.SKILL_PHASE_ROUTING == {}
        assert pp.build_skill_routing("data-analysis", "planning",
                                      "ein konzept dafuer bitte") == []

    def test_v10_debug_domain_has_no_skill_hint(self):
        # v10 (NOTES Befund 12): systematic-debugging riss die vorregistrierte
        # Latte (v9 0/8, v8 1/8 Follow) -> Zeile raus. Der Skill selbst bleibt
        # verfügbar, nur der Hook bewirbt ihn nicht mehr.
        assert "debug" not in pp.SKILL_ROUTING
        assert pp.build_skill_routing("debug", "quiet", "irgendwas ist kaputt") == []

    def test_domain_workflow_routes_subagent_briefing_only(self):
        # v9: verify-subagent-tallies (0/50 Follow) ist raus — sein Moment ist
        # der EINTREFFENDE Subagent-Report (Phase 2: PostToolUse), nicht der Prompt.
        lines = pp.build_skill_routing("workflow", "quiet", "starte ein paar subagenten")
        joined = " ".join(lines)
        assert 'Skill("subagent-briefing")' in joined
        assert "verify-subagent-tallies" not in joined

    def test_capped_at_max(self):
        # Der Deckel bleibt Kontrakt, auch wenn der v9-Bestand ihn kaum noch
        # erreichen kann (max. 1 Domain-Zeile, keine Regeln/Phase-Zeilen).
        assert pp.SKILL_HINT_MAX == 2
        lines = pp.build_skill_routing(
            "debug", "planning",
            "review mal das konzept, pytest laeuft gegen die db und es ist kaputt")
        assert len(lines) <= pp.SKILL_HINT_MAX

    def test_no_duplicate_lines(self):
        lines = pp.build_skill_routing("debug", "quiet", "pytest conftest sqlite test-db")
        assert len(lines) == len(set(lines))

    def test_none_prompt_is_safe(self):
        assert pp.build_skill_routing(None, "quiet", None) == []


class TestSkillNames:
    def test_extracts_names_in_order(self):
        assert pp.skill_names(['Skill("a") und dann Skill("b")']) == ["a", "b"]

    def test_ignores_negative_mention(self):
        # Ein "NICHT xyz" traegt keine Klammerform und darf nicht als
        # Empfehlung in der Telemetrie landen — sonst misst die Eval Unsinn.
        lines = ['Skill("review") nutzen. NICHT code-reviewer verwenden.']
        assert pp.skill_names(lines) == ["review"]

    def test_dedupes(self):
        assert pp.skill_names(['Skill("x")', 'Skill("x")']) == ["x"]

    def test_empty_input(self):
        assert pp.skill_names(None) == []
        assert pp.skill_names([]) == []


class TestSkillCompose:
    def test_block_rendered(self):
        out = pp.compose_context("debug", "quiet", ["tu X"], None,
                                 skills=['Skill("y") nutzen'])
        assert "SKILL-ROUTING" in out
        assert '- Skill("y") nutzen' in out

    def test_block_absent_without_skills(self):
        out = pp.compose_context("debug", "quiet", ["tu X"], None)
        assert "SKILL-ROUTING" not in out

    def test_skill_block_precedes_caps_block(self):
        # Design-Entscheid: die Aktion steht vor dem Hintergrundmaterial.
        out = pp.compose_context("debug", "quiet", [], ["atlas/skill:x"],
                                 skills=['Skill("y") nutzen'])
        assert out.index("SKILL-ROUTING") < out.index("VORAB-SUCHE Capability-RAG")

    def test_skills_only_still_renders(self):
        out = pp.compose_context("general", "quiet", [], None, skills=['Skill("y")'])
        assert "SKILL-ROUTING" in out
        assert "RAG-AUFTRAG" not in out

    def test_no_self_devaluation_wording(self):
        # H1-Politik gilt auch fuer den neuen Block.
        out = pp.compose_context("debug", "quiet", [], None,
                                 skills=['Skill("y") nutzen'])
        assert "optional" not in out.lower()
        assert "kein Befehl" not in out

    def test_system_message_skill_suffix(self):
        msg = pp.build_system_message("debug", "quiet", ["a"], "daemon", None, ["l1"])
        assert msg == "prelude ▸ debug · quiet · caps=1(daemon) · skill=1"

    def test_system_message_unchanged_without_skills(self):
        # Rueckwaerts-Kontrakt zu v7: ohne Skill-Hint exakt das alte Format.
        assert pp.build_system_message("debug", "quiet", ["a"], "daemon", [], []) == \
            "prelude ▸ debug · quiet · caps=1(daemon)"

    def test_system_message_mentor_and_skill_order(self):
        msg = pp.build_system_message("debug", "quiet", ["a"], "daemon", ["m"], ["s"])
        assert msg == "prelude ▸ debug · quiet · caps=1(daemon) · mentor=1 · skill=1"


class TestSkillRun:
    def _kw(self, tmp_path):
        return dict(atlas_root=str(tmp_path / "no-atlas"), state_dir=str(tmp_path),
                    log_path=str(tmp_path / "t.jsonl"), now=1000.0,
                    http_fn=lambda *a, **k: None)

    def _last_event(self, tmp_path):
        raw = (tmp_path / "t.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1]
        return _json.loads(raw)

    def test_telemetry_carries_skill_hint(self, tmp_path):
        out = pp.run({"prompt": WORKFLOW_PROMPT, "session_id": "v8a"}, **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["fired"] is True
        assert ev["skill_hint"] == ["subagent-briefing"]
        assert ev["skill_hint_count"] == 1
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "SKILL-ROUTING" in ctx

    def test_system_message_shows_skill_segment(self, tmp_path):
        out = pp.run({"prompt": WORKFLOW_PROMPT, "session_id": "v8b"}, **self._kw(tmp_path))
        assert "· skill=1" in _json.loads(out)["systemMessage"]

    def test_v10_debug_prompt_without_material_stays_silent(self, tmp_path):
        # v9 feuerte hier nur mit der Debug-Skill-Zeile (Hint-only-Feuer, in der
        # Stichprobe u. a. auf "scheint zu funktionieren…"). Ohne Caps/Mentoren
        # ist das seit v10 no_material: kein Kontext für den Agenten, nur die
        # sichtbare T-31-Skip-Zeile für den User.
        out = pp.run({"prompt": DEBUG_PROMPT, "session_id": "v10a"}, **self._kw(tmp_path))
        assert "hookSpecificOutput" not in _json.loads(out)
        assert self._last_event(tmp_path)["skip"] == "no_material"

    def test_no_skill_no_caps_skips_no_material(self, tmp_path):
        # v9-Kern: ohne Skill-Hint UND ohne Caps/Mentoren feuert nichts mehr —
        # in v8 waere dieser Prompt mit nur generischem Auftragstext gefeuert
        # (52 % aller v8-Feuerungen, Befund 10).
        out = pp.run({"prompt": "schreibe eine kurze zusammenfassung von notes.md als "
                                "fliesstext, hoechstens zehn saetze bitte",
                      "session_id": "v8c"}, **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["skip"] == "no_material"
        assert "fired" not in ev

    def test_schema_version_bumped(self, tmp_path):
        pp.run({"prompt": WORKFLOW_PROMPT, "session_id": "v8d"}, **self._kw(tmp_path))
        assert self._last_event(tmp_path)["v"] == 12


class TestNoDeadSkillReferences:
    """Regressions-Guard: der Hook darf keine Skills bewerben, die es nicht gibt.

    Anlass (2026-07-22): DOMAIN_ROUTING["debug"] empfahl `diagnose-hitl`, der
    laengst in ~/.claude/skills/_archive/ liegt, und ui-frontend empfahl
    `modern-web-design`, dessen Plugin in enabledPlugins auf false steht.
    Bewusst hermetisch — geprueft wird gegen eine feste Liste bekannter Leichen,
    nicht gegen das Dateisystem des laufenden Rechners.
    """

    # Stand 2026-07-22 nach der Aufraeumrunde. `plan-merger` steht bewusst NICHT
    # mehr hier: er wurde reaktiviert, weil der Command /merge-plans ihn braucht.
    ARCHIVED = ["diagnose-hitl", "grill-with-docs", "improve-codebase-architecture",
                "block-hook-review", "structural-assertion-hygiene", "save-session",
                "modern-web-design", "threejs-webgl", "gsap-scrolltrigger",
                "pixijs-2d", "react-three-fiber",
                # neu archiviert (0 Aufrufe ueber 3866 Sessions, beide Kanaele gezaehlt)
                "particles-gpu", "particles-lifecycle", "particles-physics",
                "particles-router", "test-validator", "brain-dump-router"]

    def _all_routing_text(self):
        # v9: DOMAIN_ROUTING existiert nicht mehr; die leeren Strukturen
        # (SKILL_PHASE_ROUTING/SKILL_RULES) bleiben absichtlich im Sweep,
        # damit ein Phase-2-Wiedereinbau automatisch mitgeprueft wird.
        parts = [pp.PLANNING_ROUTING]
        parts.extend(pp.SKILL_ROUTING.values())
        parts.extend(pp.SKILL_PHASE_ROUTING.values())
        parts.extend(line for _kws, line in pp.SKILL_RULES)
        return "\n".join(parts)

    def test_no_archived_skill_is_advertised(self):
        text = self._all_routing_text()
        found = [n for n in self.ARCHIVED if n in text]
        assert found == [], "Routing bewirbt archivierte/deaktivierte Skills: %s" % found

    def test_skill_calls_use_callable_form(self):
        # Jede positive Empfehlung muss als Skill("name") dastehen, damit
        # skill_names() sie findet und eval_skill_routing sie messen kann.
        for line in list(pp.SKILL_ROUTING.values()) + list(pp.SKILL_PHASE_ROUTING.values()):
            assert pp.skill_names([line]), "keine Skill(...)-Form in: %s" % line[:60]
