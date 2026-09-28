# -*- coding: utf-8 -*-
"""Nutzungsmessung (Idee 12): greift Claude eingespielte Treffer auf?

Relevanz (Judge) sagt nicht, ob das Material genutzt wird. Die Transkripte
enthalten die Injektion wörtlich (Anhang `hook_additional_context` direkt nach
dem Nutzer-Prompt) sowie Claudes Antwort und Tool-Aufrufe desselben Turns.

Signale je Item:
- ref:  Record-ID oder Kurzform (L140, Skill-Name, Wiki-Dateiname) steht in
        Antwort oder Tool-Input — und NICHT schon in Prompt/Vorantwort.
- echo: >= ECHO_MIN markante Begriffe (>= 6 Zeichen) aus dem Item-Text stehen
        in der Antwort, aber nicht in Prompt/Vorantwort.
Kontrolle: nicht eingespielte Nachbarn derselben Suche (gleiche Query, gleiche
Partition) — misst, was Claude ohnehin gesagt/gefunden hätte.
"""
import json
import re
from collections import defaultdict
from pathlib import Path

import prompt_prelude as pp
from replay import corpus as cp

ECHO_MIN = 2
_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß][A-Za-zÄÖÜäöüß0-9_-]{5,}")
_SHORT_ID_RE = re.compile(r"^[A-Z]{1,3}\d+$")
_GENERIC_NAMES = {"readme", "index", "claude", "agents", "changelog", "notes", "todo",
                  "plan", "spec", "summary", "memory", "soul"}
_SECTIONS = (("VORAB-SUCHE Capability", "caps"), ("VORAB-SUCHE Frühere Fälle", "mentor"),
             ("PROJEKT-KONTEXT", "project"), ("SKILL-ROUTING", "skill"))


def parse_injected(block):
    """-> [{rid, text, partition}] aus einem <prompt_prelude>-Block."""
    if not block or "<prompt_prelude" not in str(block):
        return []
    items, section = [], None
    for line in str(block).splitlines():
        for prefix, name in _SECTIONS:
            if line.startswith(prefix):
                section = name
        if section in (None, "skill") or not line.startswith("- ["):
            continue
        body = line[3:]
        if section == "project":
            rid, _, text = body.partition("] ")
            rid = rid.rstrip("]")
        else:
            body = body[:-1] if body.endswith("]") else body
            rid, _, text = body.partition(" — ")
        if rid.strip():
            items.append({"rid": rid.strip(), "text": text.strip(), "partition": section})
    return items


def short_forms(rid):
    """Kurzformen, unter denen Claude einen Record nennen würde.
    Atlas-Karten haben keine: Projekt-/Skill-Namen stehen in Pfaden, MCP-Tool-
    Namen und Tabellen, ohne dass die Karte genutzt wurde (Semantik-Befund
    2026-09-28: 13 von 16 Namens-Treffern waren Zufälle). Sie zählen nur mit
    voller ID bzw. Skills per Skill-Aufruf (siehe item_signals)."""
    rid = str(rid or "")
    if rid.startswith("atlas/"):
        return set()
    last = rid.rstrip("/").split("/")[-1]
    if _SHORT_ID_RE.match(last):
        return {last}
    name = re.sub(r"\.md$", "", last)
    if len(name) < 3 or name.lower() in _GENERIC_NAMES:
        return set()
    return {name}


def _contains(haystack, needle):
    return re.search(r"(?<![A-Za-z0-9])" + re.escape(needle.lower()) + r"(?![A-Za-z0-9])",
                     haystack) is not None


def _tokens(text):
    return {t.lower() for t in _TOKEN_RE.findall(str(text or ""))}


def item_signals(rid, text, turn):
    known = (str(turn.get("prompt") or "") + "\n" + str(turn.get("prev_assistant") or "")).lower()
    produced = (str(turn.get("answer") or "") + "\n"
                + "\n".join(turn.get("tool_inputs") or [])).lower()
    forms = {str(rid).lower()} | {f.lower() for f in short_forms(rid)}
    ref = any(_contains(produced, f) and not _contains(known, f) for f in forms)
    kind, _, name = str(rid).partition(":")
    if kind in ("atlas/skill", "atlas/plugin") and name:
        # Skill-/Plugin-Karten: nur ein echter Skill-Aufruf zählt als Nutzung.
        ref = ref or any(c == name or c.startswith(name + ":")
                         for c in turn.get("skill_calls") or [])
    stop = pp.STOP_WORDS | pp._CONTEXT_STOP
    candidates = sorted(t for t in _tokens(text) - _tokens(known) if t not in stop)
    answer_tokens = _tokens(turn.get("answer"))
    echo_tokens = [t for t in candidates if t in answer_tokens]
    return {"ref": ref, "echo_tokens": echo_tokens, "echo": len(echo_tokens) >= ECHO_MIN}


def iter_turns(path):
    """Yields je Nutzer-Prompt {session, uuid, cwd, prompt, prev_assistant,
    block, answer, tool_inputs}; block = injizierter <prompt_prelude>-Text oder None."""
    turn, prev = None, ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict) or rec.get("isSidechain"):
                continue
            msg = rec.get("message") if isinstance(rec.get("message"), dict) else {}
            if rec.get("type") == "user":
                text = cp._text_of(msg.get("content"))
                if text is not None and cp._is_human_prompt(rec, text):
                    view = cp._hook_view(text)
                    if view:
                        if turn is not None:
                            prev = turn["answer"][-cp.PREV_ANSWER_CAP:] or prev
                            yield turn
                        turn = {"session": rec.get("sessionId"), "uuid": rec.get("uuid"),
                                "cwd": rec.get("cwd"), "prompt": view, "prev_assistant": prev,
                                "block": None, "answer": "", "tool_inputs": [],
                                "skill_calls": [], "_answered": False}
                continue
            if turn is None:
                continue
            att = rec.get("attachment") if isinstance(rec.get("attachment"), dict) else None
            if att and att.get("type") == "hook_additional_context" and not turn["_answered"]:
                content = att.get("content")
                joined = "\n".join(content) if isinstance(content, list) else str(content or "")
                if "<prompt_prelude" in joined and turn["block"] is None:
                    turn["block"] = joined[joined.index("<prompt_prelude"):]
                continue
            if rec.get("type") == "assistant":
                turn["_answered"] = True
                for b in msg.get("content") or []:
                    if not isinstance(b, dict):
                        continue
                    if b.get("type") == "text" and b.get("text"):
                        turn["answer"] = (turn["answer"] + "\n" + b["text"]).lstrip("\n")
                    elif b.get("type") == "tool_use":
                        inp = b.get("input") if isinstance(b.get("input"), dict) else {}
                        turn["tool_inputs"].append(json.dumps(inp, ensure_ascii=False))
                        if b.get("name") == "Skill" and inp.get("skill"):
                            turn["skill_calls"].append(str(inp["skill"]))
    if turn is not None:
        yield turn


def aggregate(rows):
    """-> {group: {partition|'all': {n, ref, echo, any}}}"""
    acc = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    for r in rows:
        for key in (r["partition"], "all"):
            a = acc[r["group"]][key]
            a[0] += 1
            a[1] += bool(r["ref"])
            a[2] += bool(r["echo"])
            a[3] += bool(r["ref"] or r["echo"])
    return {g: {k: {"n": a[0], "ref": a[1] / a[0], "echo": a[2] / a[0], "any": a[3] / a[0]}
                for k, a in parts.items()} for g, parts in acc.items()}


def control_items(results, injected, terms):
    """Nicht eingespielte Nachbarn je Partition, gleiche Anzahl wie eingespielt."""
    inj = {i["rid"] for i in injected}
    want = defaultdict(int)
    for i in injected:
        want[i["partition"]] += 1
    out = []
    caps = [r for r in results or [] if isinstance(r, dict)
            and str(r.get("record_id", "")).startswith("atlas/") and r.get("record_id") not in inj]
    for r in caps[:want["caps"]]:
        hint = pp.format_cap_hint(r)
        out.append({"rid": r["record_id"], "text": hint.partition(" — ")[2], "partition": "caps"})
    mentors = [h for h in pp.filter_mentor_results(results, terms, limit=12)
               if h.partition(" — ")[0] not in inj]
    for h in mentors[:want["mentor"]]:
        rid, _, text = h.partition(" — ")
        out.append({"rid": rid, "text": text, "partition": "mentor"})
    return out


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Nutzung eingespielter Prelude-Treffer messen")
    ap.add_argument("--projects", default=str(Path.home() / ".claude" / "projects"))
    args = ap.parse_args(argv)
    rows, n_turns, cache = [], 0, {}
    for path in sorted(Path(args.projects).glob("*/*.jsonl")):
        try:
            turns = list(iter_turns(path))
        except OSError:
            continue
        for t in turns:
            injected = [i for i in parse_injected(t["block"]) if i["partition"] != "skill"]
            if not injected:
                continue
            n_turns += 1
            terms = pp.extract_query(t["prompt"])
            if terms not in cache:
                cache[terms] = pp.search_via_daemon(terms, 12, timeout=10.0) or []
            for group, items in (("injected", injected),
                                 ("control", control_items(cache[terms], injected, terms))):
                for i in items:
                    s = item_signals(i["rid"], i["text"], t)
                    rows.append({"group": group, "partition": i["partition"],
                                 "ref": s["ref"], "echo": s["echo"]})
    agg = aggregate(rows)
    print(f"Turns mit Injektion: {n_turns} | Items: {len(rows)}")
    print("  Gruppe     Partition  n     Referenz  Echo   eins davon")
    for group in ("injected", "control"):
        for part in ("all", "caps", "mentor", "project"):
            m = agg.get(group, {}).get(part)
            if m:
                print(f"  {group:<10} {part:<9} {m['n']:>4}  {m['ref']:>8.0%} {m['echo']:>6.0%} "
                      f"{m['any']:>10.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
