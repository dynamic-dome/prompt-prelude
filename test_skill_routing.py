# -*- coding: utf-8 -*-
"""v8 — Skill-Routing (2026-07-22).

Warum ein eigener Kanal neben dem RAG-Routing: der RAG-Kanal liefert Caps fertig
mit, der Agent kann sie passiv konsumieren — deshalb misst eval_compliance dort
nur 15%->18% (+3pp, NOTES Befund 7). Ein Skill-Body laesst sich nicht vorab
injizieren; der Agent ruft ihn auf oder nicht. Diese Tests sichern Inhalt,
Reihenfolge, Deckel und den Rueckwaerts-Kontrakt zu v7 ab.
"""
import json as _json

import prompt_prelude as pp

DEBUG_PROMPT = ("der parser wirft einen fehler beim einlesen, "
                "bitte debugge das in src/parser.py")


class TestBuildSkillRouting:
    def test_empty_without_match(self):
        assert pp.build_skill_routing("data-analysis", "quiet",
                                      "mach eine tabelle daraus bitte") == []

    def test_rule_pytest_triggers_schema_guard(self):
        lines = pp.build_skill_routing("general", "quiet",
                                       "laeuft pytest hier gegen eine echte db?")
        assert any('Skill("sqlite-schema-guard")' in l for l in lines)

    def test_rule_conftest_stem_matches(self):
        # "conftest*" ist ein Praefix-Stem -> auch "conftest.py" zieht
        lines = pp.build_skill_routing("general", "quiet",
                                       "schau in die conftest.py rein bitte")
        assert any("sqlite-schema-guard" in l for l in lines)

    def test_review_rule_carries_negative_routing(self):
        lines = pp.build_skill_routing("general", "quiet",
                                       "kannst du das nochmal reviewen bitte")
        joined = " ".join(lines)
        assert 'Skill("review")' in joined
        assert "NICHT code-reviewer" in joined
        assert "Codex" in joined

    def test_domain_debug_routes_systematic_debugging(self):
        lines = pp.build_skill_routing("debug", "quiet", "irgendwas ist kaputt")
        assert any("superpowers:systematic-debugging" in l for l in lines)

    def test_domain_workflow_routes_subagent_pair(self):
        lines = pp.build_skill_routing("workflow", "quiet", "starte ein paar subagenten")
        joined = " ".join(lines)
        assert 'Skill("subagent-briefing")' in joined
        assert 'Skill("verify-subagent-tallies")' in joined

    def test_phase_planning_separates_plan_skills(self):
        lines = pp.build_skill_routing("data-analysis", "planning",
                                       "ein konzept dafuer bitte")
        joined = " ".join(lines)
        assert "office-hours" in joined
        assert "superpowers:brainstorming" in joined
        assert "plan-ceo-review" in joined

    def test_capped_at_max(self):
        # pytest-Regel + review-Regel + debug-Domain + planning-Phase = 4 Kandidaten
        lines = pp.build_skill_routing(
            "debug", "planning",
            "review mal das konzept, pytest laeuft gegen die db und es ist kaputt")
        assert pp.SKILL_HINT_MAX == 2
        assert len(lines) == 2

    def test_rules_win_over_domain(self):
        # Prioritaet: harte Regel-Trigger vor Domain
        lines = pp.build_skill_routing("debug", "quiet", "pytest schlaegt fehl, bitte fixen")
        assert "sqlite-schema-guard" in lines[0]

    def test_no_duplicate_lines(self):
        lines = pp.build_skill_routing("debug", "quiet", "pytest conftest sqlite test-db")
        assert len(lines) == len(set(lines))

    def test_none_prompt_is_safe(self):
        assert pp.build_skill_routing(None, "quiet", None) == []

    def test_word_boundary_no_substring_false_fire(self):
        # "preview" darf die review-Regel NICHT ziehen (\b-Matching, wie bei
        # DOMAIN_HINTS nach dem ui/build-Fehlmatch-Befund).
        lines = pp.build_skill_routing("general", "quiet",
                                       "zeig mir eine preview der seite bitte")
        assert not any("Skill(\"review\")" in l for l in lines)


class TestSkillNames:
    def test_extracts_names_in_order(self):
        assert pp.skill_names(['Skill("a") und dann Skill("b")']) == ["a", "b"]

    def test_ignores_negative_mention(self):
        # "NICHT code-reviewer" traegt keine Klammerform und darf nicht als
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

    def test_skill_block_precedes_rag_block(self):
        # Design-Entscheid: die Aktion steht vor dem Hintergrundmaterial.
        out = pp.compose_context("debug", "quiet", ["tu X"], ["atlas/skill:x"],
                                 skills=['Skill("y") nutzen'])
        assert out.index("SKILL-ROUTING") < out.index("RAG-AUFTRAG")

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
        out = pp.run({"prompt": DEBUG_PROMPT, "session_id": "v8a"}, **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["fired"] is True
        assert ev["skill_hint"] == ["superpowers:systematic-debugging"]
        assert ev["skill_hint_count"] == 1
        ctx = _json.loads(out)["hookSpecificOutput"]["additionalContext"]
        assert "SKILL-ROUTING" in ctx

    def test_system_message_shows_skill_segment(self, tmp_path):
        out = pp.run({"prompt": DEBUG_PROMPT, "session_id": "v8b"}, **self._kw(tmp_path))
        assert "· skill=1" in _json.loads(out)["systemMessage"]

    def test_no_skill_hint_leaves_event_empty(self, tmp_path):
        pp.run({"prompt": "schreibe eine kurze zusammenfassung von notes.md als "
                          "fliesstext, hoechstens zehn saetze bitte",
                "session_id": "v8c"}, **self._kw(tmp_path))
        ev = self._last_event(tmp_path)
        assert ev["skill_hint"] == []
        assert ev["skill_hint_count"] == 0

    def test_schema_version_bumped(self, tmp_path):
        pp.run({"prompt": DEBUG_PROMPT, "session_id": "v8d"}, **self._kw(tmp_path))
        assert self._last_event(tmp_path)["v"] == 8


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
        parts = list(pp.DOMAIN_ROUTING.values())
        parts.append(pp.PLANNING_ROUTING)
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
