# -*- coding: utf-8 -*-
"""Replay-Korpus: echte Nutzer-Prompts aus Claude-Code-Transkripten
(docs/2026-09-28-plan-relevanz-replay.md). Fixtures sind synthetische
Transkript-Zeilen in tmp_path — nie die echten ~/.claude/projects."""
import json

from replay import corpus


def _w(path, records):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in records) + "\n",
                    encoding="utf-8")


def _user(text, uuid, **kw):
    rec = {"type": "user", "uuid": uuid, "sessionId": "s1", "isSidechain": False,
           "timestamp": "2026-09-28T07:00:00.000Z", "cwd": r"C:\Users\alice\AI\evolab",
           "entrypoint": "cli", "origin": {"kind": "human"},
           "message": {"role": "user", "content": text}}
    rec.update(kw)
    return rec


def _assistant(*texts):
    return {"type": "assistant", "sessionId": "s1", "isSidechain": False,
            "message": {"role": "assistant",
                        "content": [{"type": "text", "text": t} for t in texts]
                        + [{"type": "tool_use", "name": "Bash", "input": {"command": "ls"}}]}}


def _tool_result(uuid):
    return {"type": "user", "uuid": uuid, "sessionId": "s1", "isSidechain": False,
            "message": {"role": "user",
                        "content": [{"type": "tool_result", "content": "output"}]}}


class TestIterHumanPrompts:
    def test_extracts_prompt_with_cwd_and_previous_answer(self, tmp_path):
        p = tmp_path / "proj" / "s1.jsonl"
        _w(p, [_user("baue den lighthouse runner um", "u1"),
               _assistant("Erster Teil.", "Der Runner ist umgebaut."),
               _tool_result("t1"),
               _user("und jetzt die tests dazu bitte", "u2")])
        got = list(corpus.iter_human_prompts(p))
        assert [g["prompt"] for g in got] == ["baue den lighthouse runner um",
                                               "und jetzt die tests dazu bitte"]
        assert got[0]["prev_assistant"] == ""
        assert got[1]["prev_assistant"] == "Erster Teil.\nDer Runner ist umgebaut."
        assert got[1]["cwd"] == r"C:\Users\alice\AI\evolab"
        assert got[1]["session"] == "s1" and got[1]["uuid"] == "u2"

    def test_list_content_with_text_blocks_counts(self, tmp_path):
        p = tmp_path / "proj" / "s1.jsonl"
        _w(p, [_user([{"type": "text", "text": "prüfe die telemetrie"}], "u1")])
        assert [g["prompt"] for g in corpus.iter_human_prompts(p)] == ["prüfe die telemetrie"]

    def test_excludes_non_human_records(self, tmp_path):
        p = tmp_path / "proj" / "s1.jsonl"
        _w(p, [_user("aus einem subagenten heraus geschrieben", "u1", isSidechain=True),
               _user("headless digest prompt mit vielen woertern", "u2", entrypoint="sdk-cli"),
               _user("<task-notification>\n<summary>done</summary>", "u3"),
               _user("<cross-session-message from=\"x\">hallo</cross-session-message>", "u4"),
               _user("[Request interrupted by user]", "u5"),
               _user("vom harness erzeugt", "u6", origin={"kind": "system"}),
               _user("meta eintrag", "u7", isMeta=True),
               _user("echter prompt bleibt drin", "u8")])
        assert [g["uuid"] for g in corpus.iter_human_prompts(p)] == ["u8"]

    def test_prompts_in_hook_view(self, tmp_path):
        # Der Hook sieht Slash-Commands roh ("/name args"), Bash-Modus
        # (`! cmd`) gar nicht — das Transkript speichert beides expandiert.
        # Live-Beleg: Telemetrie hat 0 Events mit <command-message>/<bash-*>,
        # aber fired-Previews wie "/context-pack Deep auf …".
        p = tmp_path / "proj" / "s1.jsonl"
        _w(p, [_user("<command-message>nordstern-loop</command-message>\n"
                     "<command-name>/nordstern-loop</command-name>\n"
                     "<command-args>aber mit dem update starten</command-args>", "u1"),
               _user("<command-message>agentic-os:wrap-up</command-message>\n"
                     "<command-name>/agentic-os:wrap-up</command-name>", "u2"),
               _user("<bash-input>git push</bash-input>", "u3"),
               _user("<bash-stdout>To github.com</bash-stdout><bash-stderr></bash-stderr>", "u4")])
        got = [(g["uuid"], g["prompt"]) for g in corpus.iter_human_prompts(p)]
        assert got == [("u1", "/nordstern-loop aber mit dem update starten"),
                       ("u2", "/agentic-os:wrap-up")]

    def test_prev_answer_capped_to_tail(self, tmp_path):
        p = tmp_path / "proj" / "s1.jsonl"
        _w(p, [_user("start", "u1"), _assistant("x" * 5000 + "ENDE"), _user("weiter so", "u2")])
        prev = list(corpus.iter_human_prompts(p))[1]["prev_assistant"]
        assert len(prev) == corpus.PREV_ANSWER_CAP and prev.endswith("ENDE")

    def test_broken_lines_are_skipped(self, tmp_path):
        p = tmp_path / "proj" / "s1.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("{kaputt\n" + json.dumps(_user("heil geblieben", "u1")) + "\n",
                     encoding="utf-8")
        assert [g["uuid"] for g in corpus.iter_human_prompts(p)] == ["u1"]


class TestBuildCorpus:
    def test_top_level_transcripts_only_and_stable_ids(self, tmp_path):
        projects = tmp_path / "projects"
        _w(projects / "proj" / "s1.jsonl", [_user("top level prompt", "u1")])
        _w(projects / "proj" / "s1" / "subagents" / "a.jsonl", [_user("subagent prompt", "u9")])
        out = tmp_path / "corpus.jsonl"
        n_new = corpus.build_corpus(projects, out)
        rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
        assert n_new == 1
        assert [r["prompt"] for r in rows] == ["top level prompt"]
        assert rows[0]["pid"] == corpus.prompt_id("s1", "u1")

    def test_merge_keeps_entries_whose_transcript_vanished(self, tmp_path):
        # Kern-Anforderung: cleanupPeriodDays löscht Transkripte — der Snapshot
        # darf dadurch nichts verlieren und nichts doppeln.
        projects = tmp_path / "projects"
        out = tmp_path / "corpus.jsonl"
        _w(projects / "proj" / "s1.jsonl", [_user("alter prompt", "u1")])
        assert corpus.build_corpus(projects, out) == 1
        (projects / "proj" / "s1.jsonl").unlink()
        _w(projects / "proj" / "s2.jsonl", [_user("neuer prompt", "u2", sessionId="s2")])
        assert corpus.build_corpus(projects, out) == 1
        assert corpus.build_corpus(projects, out) == 0
        prompts = [json.loads(l)["prompt"] for l in out.read_text(encoding="utf-8").splitlines()]
        assert prompts == ["alter prompt", "neuer prompt"]
