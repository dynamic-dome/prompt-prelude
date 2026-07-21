#!/usr/bin/env python3
"""Wirkt das Skill-Routing? Join prompt_prelude.jsonl x Claude-Code-Transkripte.

Warum diese Eval ueberhaupt existiert
-------------------------------------
`eval_compliance.py` misst den RAG-Kanal und kommt auf 15% -> 18% (+3pp,
NOTES Befund 7). Die Zahl ist stumpf, und der Grund steht dort ausdruecklich:
die Prelude injiziert die Caps FERTIG mit, also kann der Agent sie konsumiert
haben, ohne je memory_search zu rufen. Unterlassung und Erfuellung sind nicht
trennbar.

Beim Skill-Routing gibt es dieses Schlupfloch nicht: ein SKILL.md-Body laesst
sich nicht vorab injizieren. Entweder der Agent ruft den Skill auf — dann steht
`"skill":"<name>"` im Transkript — oder er tut es nicht. Diese Eval ist damit
die saubere Antwort auf die Architekturfrage aus AGENTS.md T-4 (advisory-Kanal
vs. PreToolUse-Gate): faellt die Follow-Rate auch hier auf ~18%, ist advisory
empirisch widerlegt und das Gate ist dran.

Aufruf (manuell, nie im Hook-Pfad, nie in CI):

    python eval_skill_routing.py [--window 900] [--min-version 8]

Semantik
--------
- FOLLOW  : nach einem fired-Event mit skill_hint wurde mindestens einer der
            empfohlenen Skills in derselben Session innerhalb des Fensters
            aufgerufen (Skill-Tool ODER getippter Slash-Command).
- BASELINE: fired-Events OHNE skill_hint — wie oft wird einer der ueberhaupt
            routbaren Skills dann von selbst gerufen? Ohne diese Gegenprobe
            misst FOLLOW nur, wie oft der Agent den Skill sowieso genommen
            haette (derselbe Fehler, den Befund 7 beim RAG-Kanal aufdeckte).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import prompt_prelude as pp

# Windows-Konsole ist cp1252 — "·" in der Ausgabe wuerde sonst crashen.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

HERE = Path(__file__).resolve().parent
DEFAULT_TELEMETRY = HERE / "prompt_prelude.jsonl"
DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"
DEFAULT_WINDOW_S = 900.0     # 15 Min, gleiche Konvention wie eval_compliance
DEFAULT_MIN_VERSION = 8      # skill_hint existiert erst ab Schema v8

SKILL_CALL_RE = re.compile(r'"skill":"([^"]+)"')
CMD_NAME_RE = re.compile(r"<command-name>/?([A-Za-z0-9_:-]+)</command-name>")
TS_RE = re.compile(r'"timestamp":"([^"]+)"')


def iso_to_epoch(ts: str) -> float | None:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def iter_jsonl(path: Path):
    try:
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except ValueError:
                    continue
    except OSError:
        return


def load_fired(telemetry: Path, min_version: int):
    """[(session, t, [hints]), ...] fuer alle fired-Events ab min_version."""
    out = []
    for ev in iter_jsonl(telemetry):
        if not ev.get("fired"):
            continue
        if int(ev.get("v") or 0) < min_version:
            continue
        session = ev.get("session")
        t = ev.get("t")
        if not session or not isinstance(t, (int, float)):
            continue
        out.append((session, float(t), list(ev.get("skill_hint") or [])))
    return out


def index_transcripts(projects: Path):
    """session_id -> Pfad. Der Dateiname IST die Session-UUID."""
    idx = {}
    if not projects.is_dir():
        return idx
    for p in projects.rglob("*.jsonl"):
        idx.setdefault(p.stem, p)
    return idx


def skill_calls(path: Path):
    """[(epoch, name), ...] — Skill-Tool-Calls UND getippte Slash-Commands.

    Beide Quellen zaehlen: ein Hinweis, der den Menschen dazu bringt, /skill zu
    tippen, hat genauso gewirkt wie einer, dem der Agent selbst folgt. Genau
    diese zweite Quelle fehlte der ersten Messung am 2026-07-22 und liess
    benutzte Skills als 'nie benutzt' erscheinen.
    """
    calls = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                names = SKILL_CALL_RE.findall(line) + CMD_NAME_RE.findall(line)
                if not names:
                    continue
                m = TS_RE.search(line)
                ts = iso_to_epoch(m.group(1)) if m else None
                for n in names:
                    calls.append((ts, n))
    except OSError:
        return []
    return calls


def routable_skills():
    """Alle Skills, die das Routing ueberhaupt empfehlen KANN — aus der Konfig,
    nicht aus den beobachteten Events.

    Wichtig fuer die Baseline: leitete man die Menge aus den geloggten
    skill_hints ab, waere sie vor dem ersten Hint leer und die Gegenprobe
    lieferte ein trivial-perfektes "0/389" — eine Zahl, die nach Befund
    aussieht und keiner ist."""
    lines = list(pp.SKILL_ROUTING.values()) + list(pp.SKILL_PHASE_ROUTING.values())
    lines += [line for _kws, line in pp.SKILL_RULES]
    return set(pp.skill_names(lines))


def hit(calls, hints, t0, window):
    """Wurde ein empfohlener Skill nach t0 im Fenster aufgerufen?

    Events ohne Transkript-Timestamp zaehlen NICHT als Treffer — sonst wuerde
    ein Aufruf vor dem Hinweis als Erfolg durchgehen und die Rate schoenen.
    """
    want = set(hints)
    for ts, name in calls:
        if name not in want or ts is None:
            continue
        if t0 <= ts <= t0 + window:
            return name
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    ap.add_argument("--projects", type=Path, default=DEFAULT_PROJECTS)
    ap.add_argument("--window", type=float, default=DEFAULT_WINDOW_S)
    ap.add_argument("--min-version", type=int, default=DEFAULT_MIN_VERSION)
    args = ap.parse_args()

    fired = load_fired(args.telemetry, args.min_version)
    if not fired:
        print("Keine fired-Events mit v>=%d in %s." % (args.min_version, args.telemetry))
        print("Das Skill-Routing ist frisch — erst nach ein paar Tagen Live-Betrieb messbar.")
        return 0

    idx = index_transcripts(args.projects)
    cache: dict[str, list] = {}

    hinted = followed = 0
    base_total = base_hit = 0
    per_skill = Counter()
    per_skill_hit = Counter()
    no_transcript = 0
    routable = routable_skills()

    for session, t0, hints in fired:
        path = idx.get(session)
        if path is None:
            no_transcript += 1
            continue
        if session not in cache:
            cache[session] = skill_calls(path)
        calls = cache[session]
        if hints:
            hinted += 1
            for h in hints:
                per_skill[h] += 1
            got = hit(calls, hints, t0, args.window)
            if got:
                followed += 1
                per_skill_hit[got] += 1
        else:
            # Gegenprobe: dieselbe Frage ohne Hinweis
            base_total += 1
            if hit(calls, routable, t0, args.window):
                base_hit += 1

    def pct(a, b):
        return (100.0 * a / b) if b else 0.0

    print("Fenster: %.0fs · Schema >= v%d · Transkripte: %s"
          % (args.window, args.min_version, args.projects))
    print("fired-Events gesamt: %d (davon ohne Transkript: %d)" % (len(fired), no_transcript))
    print()
    print("routbare Skills laut Konfig: %s" % ", ".join(sorted(routable)))
    if not hinted:
        print()
        print("FOLLOW: noch keine fired-Events MIT skill_hint — die Wirksamkeit")
        print("ist erst nach ein paar Tagen Live-Betrieb messbar. Bis dahin steht")
        print("unten nur die Baseline (wie oft die Skills von selbst gerufen werden).")
    print("FOLLOW   (Hinweis gegeben, Skill danach gerufen): %d/%d = %.0f%%"
          % (followed, hinted, pct(followed, hinted)))
    print("BASELINE (kein Hinweis, Skill trotzdem gerufen):  %d/%d = %.0f%%"
          % (base_hit, base_total, pct(base_hit, base_total)))
    if hinted:
        lift = pct(followed, hinted) - pct(base_hit, base_total)
        print("LIFT: %+.1f pp" % lift)
    else:
        print("LIFT: (noch nicht bestimmbar)")
    print()
    if per_skill:
        print("%-38s %6s %6s %7s" % ("SKILL", "HINT", "FOLLOW", "RATE"))
        for name, n in per_skill.most_common():
            h = per_skill_hit[name]
            print("%-38s %6d %6d %6.0f%%" % (name, n, h, pct(h, n)))
    print()
    print("Lesehilfe: Der RAG-Kanal kam auf +3pp (NOTES Befund 7), konnte aber")
    print("'ignoriert' nicht von 'schon geliefert' trennen. Hier ist der Join sauber.")
    print("Bleibt der LIFT klein, ist der advisory-Kanal widerlegt -> AGENTS.md T-4")
    print("(PreToolUse-Gate) wieder aufmachen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
