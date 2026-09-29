# -*- coding: utf-8 -*-
"""Nutzungsmessung (Idee 12): greift Claude eingespielte Treffer auf?
Treatment = Items aus dem hook_additional_context-Anhang eines Turns;
Kontrolle = nicht eingespielte Nachbarn derselben Suche. Signale: explizite
Referenz (ID/Kurzform in Antwort oder Tool-Input) und Inhalts-Echo."""
import json

from replay import uptake as up

BLOCK = ('<prompt_prelude phase="quiet" domain="debug">\n'
         "SKILL-ROUTING (für diesen Prompt einschlägig):\n"
         '- Skill("subagent-briefing") für den Auftrag\n\n'
         "VORAB-SUCHE Capability-RAG (bereits ausgeführt — prüfe diese Treffer, bevor du selbst suchst):\n"
         "- [atlas/skill:frontend-design — Distinctive visual design guidance]\n"
         '- Vertiefung bei Bedarf: memory_search_tool("x y")\n\n'
         "VORAB-SUCHE Frühere Fälle (bereits ausgeführt — ähnliche gelöste Aufgaben):\n"
         "- [agent-memory/dco/learnings/L140 — Gepushter DCO-Code ist NICHT live]\n\n"
         "PROJEKT-KONTEXT evolab (Session-Start — Learnings, Inhalt vorab):\n"
         "- [docs-evolab/README.md] evolutionaerer Lighthouse-Optimierer mit Mutator\n"
         "</prompt_prelude>")


class TestParseInjected:
    def test_all_partitions_with_text(self):
        items = up.parse_injected(BLOCK)
        assert [(i["rid"], i["partition"]) for i in items] == [
            ("atlas/skill:frontend-design", "caps"),
            ("agent-memory/dco/learnings/L140", "mentor"),
            ("docs-evolab/README.md", "project")]
        assert items[1]["text"] == "Gepushter DCO-Code ist NICHT live"
        assert items[2]["text"] == "evolutionaerer Lighthouse-Optimierer mit Mutator"

    def test_no_block_no_items(self):
        assert up.parse_injected(None) == []
        assert up.parse_injected("irgendwas anderes") == []


class TestShortForms:
    def test_identifiers_per_record_type(self):
        assert up.short_forms("agent-memory/dco/learnings/L140") == {"L140"}
        assert up.short_forms("haupt-wiki/queries/2026-07-27-session-dco-cutover.md") == \
            {"2026-07-27-session-dco-cutover"}
        assert up.short_forms("docs-evolab/README.md") == set()  # zu generisch

    def test_atlas_cards_have_no_bare_name_form(self):
        # Semantik-Befund 2026-09-28: Projektnamen stehen in Pfaden, MCP-Tool-
        # Namen und Tabellen, ohne dass die Karte genutzt wurde (13 von 16
        # "Referenzen" waren solche Zufälle). Atlas-Karten zählen nur mit
        # voller ID, Skills nur per Skill-Aufruf.
        assert up.short_forms("atlas/project-deep:job-radar") == set()
        assert up.short_forms("atlas/skill:frontend-design") == set()


class TestSignals:
    TURN = {"prompt": "der server zeigt noch den alten stand",
            "prev_assistant": "",
            "answer": "Laut L140 ist gepushter Code nicht live, solange der Server "
                      "nicht neu startet. Ich starte ihn neu.",
            "tool_inputs": ['{"record_id": "atlas/skill:frontend-design"}']}

    def test_reference_via_short_form_in_answer(self):
        s = up.item_signals("agent-memory/dco/learnings/L140",
                            "Gepushter DCO-Code ist NICHT live", self.TURN)
        assert s["ref"] is True

    def test_reference_via_full_id_in_tool_input(self):
        s = up.item_signals("atlas/skill:frontend-design", "visual design", self.TURN)
        assert s["ref"] is True

    def test_project_name_in_path_is_no_reference(self):
        turn = dict(self.TURN, tool_inputs=['{"command": "cd /c/Users/alice/AI/job-radar; ls"}'],
                    answer="Im job-radar-Ordner liegt das Skript.")
        assert up.item_signals("atlas/project-deep:job-radar", "Job-Matching", turn)["ref"] is False

    def test_skill_counts_only_when_invoked(self):
        mentioned = dict(self.TURN, answer="Layout mit frontend-design-Skill überarbeiten",
                         tool_inputs=[], skill_calls=[])
        invoked = dict(mentioned, skill_calls=["frontend-design"])
        assert up.item_signals("atlas/skill:frontend-design", "x", mentioned)["ref"] is False
        assert up.item_signals("atlas/skill:frontend-design", "x", invoked)["ref"] is True

    def test_echo_counts_distinctive_new_tokens(self):
        s = up.item_signals("x/y", "Gepushter Code bleibt ohne Neustart alt", self.TURN)
        # "gepushter" steht in der Antwort, nicht im Prompt -> 1 Echo-Token
        assert s["echo_tokens"] == ["gepushter"]
        assert s["echo"] is False  # Schwelle 2

    def test_tokens_already_in_prompt_do_not_count(self):
        turn = dict(self.TURN, prompt="gepushter code läuft nicht, server neu starten")
        s = up.item_signals("x/y", "Gepushter Code braucht Neustart", turn)
        assert "gepushter" not in s["echo_tokens"]

    def test_short_form_already_in_prompt_is_no_reference(self):
        turn = dict(self.TURN, prompt="was sagt L140 nochmal zum server")
        s = up.item_signals("agent-memory/dco/learnings/L140", "egal", turn)
        assert s["ref"] is False


class TestIterTurns:
    def _w(self, tmp_path, records):
        p = tmp_path / "t.jsonl"
        p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                     encoding="utf-8")
        return p

    def test_turn_collects_block_answer_and_tools(self, tmp_path):
        user = lambda text, uuid: {"type": "user", "uuid": uuid, "sessionId": "s",
                                   "entrypoint": "cli", "origin": {"kind": "human"},
                                   "cwd": "C:/x", "message": {"content": text}}
        recs = [user("erster prompt ohne injektion", "u1"),
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "A1"}]}},
                user("zweiter prompt mit injektion", "u2"),
                {"type": "attachment", "attachment": {"type": "hook_additional_context",
                                                      "hookEvent": "UserPromptSubmit",
                                                      "content": [BLOCK]}},
                {"type": "assistant", "message": {"content": [
                    {"type": "text", "text": "Antwort mit L140"},
                    {"type": "tool_use", "name": "Read", "input": {"file_path": "C:/a.md"}},
                    {"type": "tool_use", "name": "Skill", "input": {"skill": "review"}}]}},
                {"type": "user", "message": {"content": [{"type": "tool_result", "content": "x"}]}},
                {"type": "assistant", "message": {"content": [{"type": "text", "text": "Fertig."}]}},
                user("dritter prompt", "u3")]
        turns = list(up.iter_turns(self._w(tmp_path, recs)))
        assert [t["uuid"] for t in turns] == ["u1", "u2", "u3"]
        t2 = turns[1]
        assert t2["block"] == BLOCK and turns[0]["block"] is None
        assert t2["answer"] == "Antwort mit L140\nFertig."
        assert t2["tool_inputs"] == ['{"file_path": "C:/a.md"}', '{"skill": "review"}']
        assert t2["skill_calls"] == ["review"]
        assert t2["prev_assistant"] == "A1"


class TestAggregate:
    def test_rates_per_group(self):
        rows = [{"group": "injected", "partition": "caps", "ref": True, "echo": False},
                {"group": "injected", "partition": "caps", "ref": False, "echo": True},
                {"group": "control", "partition": "caps", "ref": False, "echo": False},
                {"group": "control", "partition": "caps", "ref": False, "echo": True}]
        agg = up.aggregate(rows)
        assert agg["injected"]["all"] == {"n": 2, "ref": 0.5, "echo": 0.5, "any": 1.0}
        assert agg["control"]["caps"] == {"n": 2, "ref": 0.0, "echo": 0.5, "any": 0.5}
