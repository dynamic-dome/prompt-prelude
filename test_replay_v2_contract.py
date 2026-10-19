# -*- coding: utf-8 -*-
"""Prelude v2: Vertrag (Toepfe, regelbasierter Anfragebau, Gate-Prompt, Antwort-Parser).
Alle Eingaben sind erfundene Beispiele."""
import json

import prompt_prelude as pp
from replay import v2_contract as v2


def _pools():
    return {
        "learnings": [{"cid": "l01", "rid": "agent-memory/x/learnings/L1", "summary": "Pfad lazy aufloesen"},
                      {"cid": "l02", "rid": "native-a/b", "summary": "zweite"},
                      {"cid": "l03", "rid": "native-a/c", "summary": "dritte"}],
        "skills": [{"cid": "s01", "rid": "atlas/skill:foo", "snippet": "Foo hilft. Mehr."},
                   {"cid": "s02", "rid": "atlas/skill:bar", "snippet": "Bar"},
                   {"cid": "s03", "rid": "atlas/skill:baz", "snippet": "Baz"}],
        "capabilities": [{"cid": "c01", "rid": "atlas/mcp-server:q", "snippet": "Q"}],
    }


def _reply(**kw):
    base = {"learnings": [], "skills": [], "capabilities": [], "clarify": ""}
    base.update(kw)
    return json.dumps(base, ensure_ascii=False)


class TestConstants:
    def test_pots_match_the_contract(self):
        assert set(v2.POTS) == {"learnings", "skills", "capabilities"}
        assert v2.POTS["learnings"]["prefixes"] == (
            "agent-memory/", "native-", "codex-native/", "shared-memory/", "membrain/",
            "haupt-wiki/queries/")
        assert v2.POTS["learnings"]["demote"] == ("haupt-wiki/queries/",)
        assert (v2.POTS["learnings"]["k"], v2.POTS["learnings"]["keep"]) == (12, 2)
        assert v2.POTS["skills"] == {"prefixes": ("atlas/skill:", "atlas/subagent:"), "k": 8, "keep": 2}
        assert v2.POTS["capabilities"] == {"prefixes": ("atlas/mcp-server:", "atlas/plugin:"),
                                           "k": 8, "keep": 2}
        assert v2.START_LEARNINGS_KEEP == 3

    def test_caps(self):
        assert (v2.PROMPT_CAP, v2.CONTEXT_CAP, v2.QUERY_TERMS_MAX, v2.BUDGET_MS) == (1500, 600, 10, 4000)

    def test_stop_words_v2_is_a_tuple_containing_todays_stop_words(self):
        assert isinstance(v2.STOP_WORDS_V2, tuple)
        assert set(pp.STOP_WORDS) <= set(v2.STOP_WORDS_V2)
        assert {"the", "and", "und", "nicht"} <= set(v2.STOP_WORDS_V2)


class TestCleanText:
    def test_strips_paths_urls_and_code(self):
        t = v2.clean_text('Schau in C:\\Users\\domes\\x\\main.py und https://a.b/c ```py\nx=1\n``` '
                          'warum hängt der Daemon')
        assert "C:" not in t and "https" not in t and "x=1" not in t and "Daemon" in t

    def test_strips_posix_paths_inline_code_and_stack_traces(self):
        t = v2.clean_text("Fehler in /home/u/proj/main.py und `foo(bar)`\n"
                          "Traceback (most recent call last):\n"
                          '  File "/a/b.py", line 3, in run\n'
                          "    at Object.run (/x/y.js:1:2)\n"
                          "Der Netzwächter hängt")
        assert "/home" not in t and "foo(bar)" not in t and "Traceback" not in t
        assert "File" not in t and "Object.run" not in t
        assert "Netzwächter" in t

    def test_keeps_umlauts_and_slash_words(self):
        assert v2.clean_text("Größe und/oder Straße") == "Größe und/oder Straße"

    def test_unterminated_code_fence_is_dropped_to_the_end(self):
        assert v2.clean_text("vorher ```py\nx=1\nund weiter") == "vorher"

    def test_caps_at_prompt_cap(self):
        assert len(v2.clean_text("x " * 5000)) <= v2.PROMPT_CAP


class TestKeyTerms:
    def test_keep_umlauts_and_drop_stopwords(self):
        assert v2.key_terms("Könntest du bitte die Größenberechnung im Straßenverzeichnis prüfen")[:2] == \
            ["Größenberechnung", "Straßenverzeichnis"]

    def test_dedupes_casefold_and_is_stable(self):
        a = v2.key_terms("Daemon daemon DAEMON Isolationsguard")
        assert [t.casefold() for t in a].count("daemon") == 1
        assert a == v2.key_terms("Daemon daemon DAEMON Isolationsguard")

    def test_hyphenated_terms_rank_high_and_limit_applies(self):
        words = " ".join(f"wort{c}x" for c in "abcdefghijklmnop")
        assert len(v2.key_terms(words)) == v2.QUERY_TERMS_MAX
        assert len(v2.key_terms(words, limit=3)) == 3
        assert v2.key_terms("alpha beta-gamma delta")[0] == "beta-gamma"

    def test_sentence_initial_capital_is_no_signal(self):
        # gleich lang: das Wort mitten im Satz gewinnt gegen das Satzanfangswort
        assert v2.key_terms("Zeitgeist und Netzwerk")[0] == "Netzwerk"


class TestBuildQuery:
    def test_adds_terms_from_previous_answer(self):
        q = v2.build_query("mach das so", "Der Netzwächter blockiert getaddrinfo im Isolationsguard")
        assert "Netzwächter" in q.split() and len(q.split()) <= v2.QUERY_TERMS_MAX + 3

    def test_empty_for_trivial_prompt(self):
        assert v2.build_query("ok", "") == ""

    def test_previous_answer_terms_do_not_repeat_prompt_terms(self):
        q = v2.build_query("Daemon hängt", "Der Daemon wartet auf Netzwächter")
        assert [t.casefold() for t in q.split()].count("daemon") == 1

    def test_only_the_tail_of_the_previous_answer_counts(self):
        prev = "Urzeitbegriff " + "x " * 400 + "Endbegriff"
        q = v2.build_query("ok", prev)
        assert "Endbegriff" in q and "Urzeitbegriff" not in q

    def test_paths_in_the_prompt_do_not_reach_the_query(self):
        assert "domes" not in v2.build_query(r"Fehler in C:\Users\domes\proj\main.py beim Daemon", "").split()


class TestGatePrompt:
    def test_system_prompt_states_the_contract(self):
        s = v2.GATE_SYSTEM_V2
        assert '{"learnings": [{"cid": "...", "why": "..."}], "skills": [...], "capabilities": [...], ' \
               '"clarify": "<leer oder ein Satz>"}' in s
        assert "Im Zweifel keinen. Ein Eintrag, der in eine falsche Richtung lenken könnte, wird weggelassen." in s
        assert "Widerspricht ein Eintrag erkennbar dem Prompt, lass ihn weg." in s
        assert "why" in s and "Pflicht" in s and "clarify" in s and "unverständlich" in s
        assert "Höchstens 2 Einträge je Topf" in s and "höchstens 3" in s

    def test_render_has_all_sections_and_candidate_lines(self):
        out = v2.render_gate_prompt("Mach das", "letzte Antwort", "C:\\proj", _pools())
        assert out.startswith("PROMPT:\nMach das\n")
        assert "ARBEITSORDNER: C:\\proj" in out
        assert "KANDIDATEN learnings" in out and "KANDIDATEN skills" in out and "KANDIDATEN capabilities" in out
        assert "l01 | agent-memory/x/learnings/L1 | Pfad lazy aufloesen" in out
        assert "s01 | atlas/skill:foo | Foo hilft." in out  # erster Satz des Auszugs

    def test_render_caps_prompt_context_and_candidate_line(self):
        pools = {"learnings": [{"cid": "l01", "rid": "r", "summary": "w " * 300}], "skills": [], "capabilities": []}
        out = v2.render_gate_prompt("p" * 5000, "a" * 1000 + "ENDE", "/w", pools)
        assert "p" * v2.PROMPT_CAP in out and "p" * (v2.PROMPT_CAP + 1) not in out
        assert "ENDE" in out and "a" * (v2.CONTEXT_CAP - 4) in out and "a" * (v2.CONTEXT_CAP - 3) not in out
        line = [ln for ln in out.splitlines() if ln.startswith("l01 | ")][0]
        assert len(line.split(" | ", 2)[2]) <= 200

    def test_render_marks_empty_pots_and_flattens_whitespace(self):
        pools = {"learnings": [{"cid": "l01", "rid": "r", "summary": "a\n b\t c"}], "skills": [], "capabilities": []}
        out = v2.render_gate_prompt("p", "", "/w", pools)
        assert "l01 | r | a b c" in out
        assert out.count("(keine)") == 2


class TestParseReply:
    def test_ignores_unknown_cids_and_caps_per_pot(self):
        r = v2.parse_reply(_reply(
            learnings=[{"cid": "l01", "why": "a"}, {"cid": "l02", "why": "b"},
                       {"cid": "l03", "why": "c"}, {"cid": "x9", "why": "d"}],
            skills=[{"cid": "s01", "why": "a"}, {"cid": "s02", "why": "b"}, {"cid": "s03", "why": "c"}]),
            _pools(), False)
        assert [k["cid"] for k in r["kept"]["learnings"]] == ["l01", "l02"]
        assert [k["cid"] for k in r["kept"]["skills"]] == ["s01", "s02"]
        assert r["is_parsed"] is True

    def test_first_prompt_of_a_session_allows_three_learnings(self):
        learn = [{"cid": c, "why": "w"} for c in ("l01", "l02", "l03")]
        r = v2.parse_reply(_reply(learnings=learn), _pools(), True)
        assert [k["cid"] for k in r["kept"]["learnings"]] == ["l01", "l02", "l03"]

    def test_garbage_keeps_nothing(self):
        empty = {"learnings": [], "skills": [], "capabilities": []}
        for bad in ("kein json", "", "{kaputt", "[1, 2]", "null", "{}}{"):
            r = v2.parse_reply(bad, _pools(), False)
            assert r == {"kept": empty, "clarify": "", "is_parsed": False}, bad

    def test_json_inside_prose_or_fences_is_found(self):
        r = v2.parse_reply("Hier:\n```json\n" + _reply(skills=[{"cid": "s02", "why": "x"}]) + "\n```",
                           _pools(), False)
        assert [k["cid"] for k in r["kept"]["skills"]] == ["s02"]

    def test_entries_without_why_are_dropped(self):
        r = v2.parse_reply(_reply(learnings=[{"cid": "l01"}, {"cid": "l02", "why": "  "},
                                             {"cid": "l03", "why": "ok"}]), _pools(), False)
        assert [k["cid"] for k in r["kept"]["learnings"]] == ["l03"]

    def test_cid_from_another_pot_is_dropped(self):
        r = v2.parse_reply(_reply(learnings=[{"cid": "s01", "why": "x"}]), _pools(), False)
        assert r["kept"]["learnings"] == []

    def test_duplicate_cids_count_once_and_bad_shapes_are_ignored(self):
        r = v2.parse_reply(_reply(learnings=[{"cid": "l01", "why": "a"}, {"cid": "l01", "why": "b"},
                                             "l02", 7, None, {"cid": 3, "why": "x"}, {"cid": "l02", "why": 5}],
                                  skills="s01"), _pools(), False)
        assert [k["cid"] for k in r["kept"]["learnings"]] == ["l01"]
        assert r["kept"]["skills"] == []

    def test_why_and_clarify_are_trimmed_and_capped(self):
        r = v2.parse_reply(_reply(learnings=[{"cid": "l01", "why": " " + "w" * 400}],
                                  clarify="  vermutlich gemeint: X\n" + "y" * 400), _pools(), False)
        assert len(r["kept"]["learnings"][0]["why"]) == 160
        assert 0 < len(r["clarify"]) <= 200 and "\n" not in r["clarify"]

    def test_clarify_alone_is_a_valid_reply(self):
        r = v2.parse_reply(_reply(clarify="die Pipeline"), _pools(), False)
        assert r["clarify"] == "die Pipeline" and r["is_parsed"] is True
        assert all(v == [] for v in r["kept"].values())

    def test_non_string_clarify_is_ignored(self):
        r = v2.parse_reply(_reply(clarify=["x"]), _pools(), False)
        assert r["clarify"] == ""
