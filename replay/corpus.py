# -*- coding: utf-8 -*-
"""Replay-Korpus aus Claude-Code-Transkripten.

Sammelt echte, getippte Nutzer-Prompts interaktiver Sessions samt Arbeitsordner
(`cwd`) und der letzten Assistant-Antwort davor. Der Snapshot ist append-only:
Claude Code löscht Transkripte nach `cleanupPeriodDays` (Default 30, NOTES
Befund 12) — bereits gesammelte Prompts bleiben erhalten.

Der Snapshot enthält volle Prompts und liegt deshalb in `replay/data/`
(gitignored — das Repo ist öffentlich).
"""
import hashlib
import json
import re
from pathlib import Path

import prompt_prelude as pp

PREV_ANSWER_CAP = 2000
_INTERRUPT_PREFIX = "[Request interrupted"


def prompt_id(session, uuid):
    return hashlib.sha1(f"{session}:{uuid}".encode("utf-8")).hexdigest()[:12]


def _text_of(content):
    """Prompt-Text aus str- oder Block-Content; None, wenn es kein reiner
    Text-Prompt ist (tool_result & Co. sind Harness-Rückgaben)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list) and content:
        if all(isinstance(b, dict) and b.get("type") == "text" for b in content):
            return "\n".join(str(b.get("text") or "") for b in content)
    return None


def _is_human_prompt(rec, text):
    if rec.get("type") != "user" or rec.get("isSidechain") or rec.get("isMeta"):
        return False
    origin = rec.get("origin")
    if isinstance(origin, dict) and origin.get("kind") not in (None, "human"):
        return False
    if pp.is_headless(rec.get("entrypoint")):
        return False
    s = text.strip()
    if not s or s.startswith(_INTERRUPT_PREFIX):
        return False
    return not s.lower().startswith(pp.MACHINE_PROMPT_MARKERS)


_CMD_NAME_RE = re.compile(r"<command-name>\s*(.*?)\s*</command-name>", re.S)
_CMD_ARGS_RE = re.compile(r"<command-args>\s*(.*?)\s*</command-args>", re.S)
_BASH_MODE_PREFIXES = ("<bash-input>", "<bash-stdout>", "<bash-stderr>")


def _hook_view(text):
    """Prompt so, wie ihn der UserPromptSubmit-Hook sieht. Slash-Commands stehen
    im Transkript expandiert (<command-message>…), der Hook bekommt "/name args";
    Bash-Modus (`! cmd`) erreicht den Hook gar nicht -> None."""
    s = text.strip()
    if s.startswith(_BASH_MODE_PREFIXES):
        return None
    if s.startswith("<command-message>"):
        name = _CMD_NAME_RE.search(s)
        if not name:
            return None
        args = _CMD_ARGS_RE.search(s)
        return " ".join(p for p in (name.group(1), args.group(1) if args else "") if p)
    return s


def iter_human_prompts(path):
    """Yields {session, uuid, ts, cwd, entrypoint, prompt, prev_assistant}."""
    turn_text = []  # Assistant-Text seit dem letzten Nutzer-Prompt
    prev = ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            if rec.get("type") == "assistant" and not rec.get("isSidechain"):
                for b in msg.get("content") or []:
                    if isinstance(b, dict) and b.get("type") == "text" and b.get("text"):
                        turn_text.append(str(b["text"]))
                continue
            text = _text_of(msg.get("content"))
            if text is None or not _is_human_prompt(rec, text):
                continue
            text = _hook_view(text)
            if not text:
                continue
            if turn_text:
                prev = "\n".join(turn_text)[-PREV_ANSWER_CAP:]
                turn_text = []
            yield {"session": rec.get("sessionId"), "uuid": rec.get("uuid"),
                   "ts": rec.get("timestamp"), "cwd": rec.get("cwd"),
                   "entrypoint": rec.get("entrypoint"), "prompt": text,
                   "prev_assistant": prev}


def build_corpus(projects_dir, out_path):
    """Neue Prompts aus allen Top-Level-Transkripten an den Snapshot anhängen.
    Subagent-Transkripte (<session>/subagents/) bleiben außen vor.
    -> Anzahl neu hinzugefügter Einträge."""
    out_path = Path(out_path)
    seen = set()
    if out_path.exists():
        for line in out_path.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["pid"])
            except (ValueError, KeyError, TypeError):
                continue
    new_rows = []
    for path in sorted(Path(projects_dir).glob("*/*.jsonl")):
        try:
            for row in iter_human_prompts(path):
                pid = prompt_id(row["session"], row["uuid"])
                if pid in seen:
                    continue
                seen.add(pid)
                new_rows.append({"pid": pid, **row})
        except OSError:
            continue
    if new_rows:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fh:
            for row in new_rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return len(new_rows)


def main(argv=None):
    import argparse
    here = Path(__file__).resolve().parent
    ap = argparse.ArgumentParser(description="Replay-Korpus aus Transkripten fortschreiben")
    ap.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"))
    ap.add_argument("--out", default=str(here / "data" / "corpus.jsonl"))
    args = ap.parse_args(argv)
    n = build_corpus(args.projects, args.out)
    total = sum(1 for _ in open(args.out, encoding="utf-8")) if Path(args.out).exists() else 0
    print(f"neu: {n} | Korpus gesamt: {total} | {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
