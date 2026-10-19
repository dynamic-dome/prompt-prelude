# -*- coding: utf-8 -*-
"""Prelude v2: der Vertrag zwischen Messbank (Python) und Mod prelude-gate (TypeScript).

Reine Funktionen, nur stdlib. Alles, was hier steht, wird vom Generator in den Mod
gespiegelt (tools/gen_parity_fixture.py); die Paritaetsfaelle halten beide Seiten gleich.
Spec: docs/superpowers/specs/2026-10-19-prelude-einspielen-design.md (Abschnitte 6 und 7).
"""
import json
import re

POTS = {
    "learnings": {
        "prefixes": ("agent-memory/", "native-", "codex-native/", "shared-memory/", "membrain/",
                     "haupt-wiki/queries/"),
        "demote": ("haupt-wiki/queries/",),
        "k": 12,
        "keep": 2,
    },
    "skills": {"prefixes": ("atlas/skill:", "atlas/subagent:"), "k": 8, "keep": 2},
    "capabilities": {"prefixes": ("atlas/mcp-server:", "atlas/plugin:"), "k": 8, "keep": 2},
}
START_LEARNINGS_KEEP = 3

PROMPT_CAP = 1500
CONTEXT_CAP = 600
QUERY_TERMS_MAX = 10
BUDGET_MS = 4000

_LINE_CAP = 200
_WHY_CAP = 160
_CLARIFY_CAP = 200
_PREV_TERMS = 3
_LEN_SCORE_CAP = 12

STOP_WORDS_V2 = tuple(sorted(set("""
ich du er sie es wir ihr mich mir dich dir uns euch ihn ihm ihnen man
das die der den dem des ein eine einen einem einer eines kein keine keinen keinem keiner
ist sind war waren bin bist sei seid wird werden wurde wurden hab habe hast hat haben hatte hatten
bitte kannst kann könnte könntest koennte koenntest soll sollte sollen sollst muss musst müssen
darf dürfen will willst wollen möchte moechte würde wuerde würdest wuerdest
und oder aber doch denn weil dass ob wenn dann als auch noch nur schon mal nicht kein
wie was wer wo wann warum wieso weshalb welche welcher welches
mit von zu zum zur bei beim nach aus auf an am im in um über ueber unter vor für fuer ohne durch gegen
so da dort hier jetzt nun gerade bereits immer wieder sehr ganz etwas alles nichts mehr
mach machs machen mache machst lass lassen gib gehen geh sag sagen schauen schau guck
erstmal einfach gerne gern eigentlich vielleicht okay ok ja nein genau
wirklich irgendwie quasi super danke dank vielen
the a an and or but if then than that this these those there here
is are was were be been being am do does did done have has had having will would shall should can could may might must
i you he she it we they me him her us them my your his its our their
to of in on at by for with from into onto over under about as not no yes
please just also very really maybe now what why how when where which who whom
let lets make get got set use used using need needs want wants thanks thank
""".split())))

_TOKEN_RE = re.compile(r"[A-Za-zÄÖÜäöüß][\w\-ÄÖÜäöüß]{2,}")
_STOP_SET = frozenset(STOP_WORDS_V2)

_FENCE_RE = re.compile(r"```.*?```", re.S)
_OPEN_FENCE_RE = re.compile(r"```.*", re.S)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")
_TRACE_RE = re.compile(
    r"^[ \t]*(?:Traceback \(most recent call last\):.*"
    r"|File \".*\", line \d+.*"
    r"|at[ \t]+[\w$.<>\[\] ]+\(.*\)[ \t]*"
    r"|at[ \t]+\S+:\d+.*)$", re.M)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+")
_WIN_QUOTED_RE = re.compile(r"\"[A-Za-z]:[\\/][^\"\n]*\"|'[A-Za-z]:[\\/][^'\n]*'")
_WIN_PATH_RE = re.compile(r"[A-Za-z]:[\\/]\S*|\\\\[\w.$-]+\\\S*")
_POSIX_PATH_RE = re.compile(
    r"(?<![\w/.:~-])(?:(?:~|\.{1,2})(?:/[\w.\-~@+]+)+|(?:/[\w.\-~@+]+){2,})/?")
_SPACE_RE = re.compile(r"\s+")


def _strip_noise(text):
    """Pfade, URLs, Code-Bloecke, Inline-Code und Stack-Trace-Zeilen raus; Leerraum zusammengezogen, ungekappt."""
    t = _FENCE_RE.sub(" ", str(text))
    t = _OPEN_FENCE_RE.sub(" ", t)
    t = _INLINE_CODE_RE.sub(" ", t)
    t = _TRACE_RE.sub(" ", t)
    t = _URL_RE.sub(" ", t)
    t = _WIN_QUOTED_RE.sub(" ", t)
    t = _WIN_PATH_RE.sub(" ", t)
    t = _POSIX_PATH_RE.sub(" ", t)
    return _SPACE_RE.sub(" ", t).strip()


def clean_text(text):
    """Pfade, URLs, Code-Bloecke, Inline-Code und Stack-Trace-Zeilen raus; auf PROMPT_CAP gekappt."""
    return _strip_noise(text)[:PROMPT_CAP].rstrip()


def _context_tail(prev_answer):
    """Letzte CONTEXT_CAP Zeichen der BEREINIGTEN Antwort (erst bereinigen, dann kappen, sonst liest
    ein Schnitt im Code-Block das schliessende ``` als offenen Zaun). Ungekappte Bereinigung, damit
    das Ende der Antwort bleibt. Beginnt der Schnitt mitten im Wort, faellt dieses Bruchstueck weg."""
    cleaned = _strip_noise(prev_answer)
    if len(cleaned) <= CONTEXT_CAP:
        return cleaned
    tail = cleaned[-CONTEXT_CAP:]
    if not cleaned[-CONTEXT_CAP - 1].isspace():
        tail = _SPACE_RE.sub(" ", tail).split(" ", 1)[1] if " " in tail else ""
    return tail


def _is_sentence_start(text, pos):
    prefix = text[:pos]
    stripped = prefix.rstrip()
    return not stripped or stripped[-1] in ".!?:" or "\n" in prefix[len(stripped):]


def key_terms(text, limit=QUERY_TERMS_MAX):
    """Schluesselbegriffe nach Seltenheit: Laenge (bei 12 gedeckelt), Bindestrich/Unterstrich,
    Grossschreibung mitten im Satz. Stabil (Gleichstand = Reihenfolge im Text), ohne Dubletten."""
    text = str(text)
    scored, seen = [], set()
    for m in _TOKEN_RE.finditer(text):
        tok = m.group(0)
        key = tok.casefold()
        if key in _STOP_SET or key in seen:
            continue
        seen.add(key)
        score = min(len(tok), _LEN_SCORE_CAP)
        if "-" in tok or "_" in tok:
            score += 3
        if tok[0].isupper() and not _is_sentence_start(text, m.start()):
            score += 2
        scored.append((-score, m.start(), tok))
    scored.sort()
    return [tok for _, _, tok in scored[:limit]]


def build_query(prompt, prev_answer):
    """Anfrage fuer den Atlas-Daemon: Begriffe des Prompts plus bis zu 3 neue aus dem Ende
    der letzten Antwort (Reihenfolge: bereinigen, dann die letzten CONTEXT_CAP Zeichen; der TS-Spiegel
    muss sie einhalten). Leer, wenn es keine Begriffe gibt."""
    terms = key_terms(clean_text(prompt))
    have = {t.casefold() for t in terms}
    tail = _context_tail(prev_answer)
    extra = [t for t in key_terms(tail, QUERY_TERMS_MAX + _PREV_TERMS) if t.casefold() not in have]
    return " ".join(terms + extra[:_PREV_TERMS])


def _keep_limits():
    return {pot: spec["keep"] for pot, spec in POTS.items()}


GATE_SYSTEM_V2 = "\n".join([
    "Du bist das Relevanz-Gate vor einem Coding-Assistenten.",
    "Ein Hook hat zu einem Nutzer-Prompt Kandidaten aus einem persönlichen Wissensindex gefunden, "
    "getrennt in drei Töpfe: learnings (Hinweise aus früheren Fällen), skills, capabilities (Werkzeuge).",
    "Wähle die wenigen aus, die dem Assistenten bei genau diesem Prompt in genau diesem Gesprächsstand "
    "helfen. Meistens ist die richtige Antwort: keiner.",
    "",
    "Behalte einen Kandidaten nur, wenn ein erfahrener Kollege, der Prompt und Gesprächsstand kennt, "
    "sagen würde: „gut, dass du daran erinnerst“ (bekannte Falle, frühere Entscheidung, frühere Lösung "
    "desselben Problems, passender Skill oder passendes Werkzeug).",
    "Nicht behalten: thematisch verwandt, aber für die konkrete Aufgabe nicht nützlich; nur Wortgleichheit; "
    "ein anderes Projekt; allgemeiner Hintergrund.",
    "",
    f"Höchstens {POTS['learnings']['keep']} Einträge je Topf "
    f"(learnings beim ersten Prompt einer Sitzung, erkennbar am leeren Ende der vorherigen Antwort: "
    f"höchstens {START_LEARNINGS_KEEP}). Im Zweifel keinen. Ein Eintrag, der in eine falsche Richtung "
    "lenken könnte, wird weggelassen. Widerspricht ein Eintrag erkennbar dem Prompt, lass ihn weg.",
    "Zu jedem behaltenen Eintrag ist why Pflicht: ein Satz, höchstens 12 Wörter, was er für genau diesen "
    "Prompt bringt. Ohne tragfähiges why wird der Eintrag nicht behalten.",
    "clarify nur, wenn der Prompt unverständlich ist und du sicher sagen kannst, was gemeint ist: "
    "ein Satz, sonst leer.",
    "",
    "Antworte ausschließlich mit einem JSON-Objekt in einer Zeile:",
    '{"learnings": [{"cid": "...", "why": "..."}], "skills": [...], "capabilities": [...], '
    '"clarify": "<leer oder ein Satz>"}',
])


def _candidate_line(cand):
    """Kernsatz eines Kandidaten: line, sonst summary, sonst erster Satz des Auszugs; eine Zeile, gekappt."""
    core = cand.get("line") or cand.get("summary")
    if not core:
        snippet = _SPACE_RE.sub(" ", str(cand.get("snippet") or "")).strip()
        core = re.split(r"(?<=[.!?])\s", snippet, maxsplit=1)[0]
    return _SPACE_RE.sub(" ", str(core)).strip()[:_LINE_CAP]


def render_gate_prompt(prompt, prev_answer, cwd, pools):
    """Der Nutzer-Prompt, den Haiku live bekommt; die Messbank nutzt exakt diesen Text."""
    out = ["PROMPT:", str(prompt)[:PROMPT_CAP], "",
           "ENDE DER VORHERIGEN ANTWORT (leer = Session-Anfang):", str(prev_answer)[-CONTEXT_CAP:], "",
           f"ARBEITSORDNER: {cwd}"]
    for pot in POTS:
        out += ["", f"KANDIDATEN {pot} (cid | Kennung | Kernsatz):"]
        cands = pools.get(pot) or []
        out += [f"{c['cid']} | {c['rid']} | {_candidate_line(c)}" for c in cands] or ["(keine)"]
    return "\n".join(out)


def _empty_parse(is_parsed=False, clarify=""):
    return {"kept": {pot: [] for pot in POTS}, "clarify": clarify, "is_parsed": is_parsed}


def parse_reply(reply, pools, is_start):
    """Haiku-Antwort -> behaltene Eintraege je Topf. Unbekannte oder topf-fremde cid, fehlendes why,
    Dubletten und Ueberzaehliges fallen weg; kaputte Antworten behalten nichts."""
    reply = str(reply)
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end <= start:
        return _empty_parse()
    try:
        data = json.loads(reply[start:end + 1])
    except ValueError:
        return _empty_parse()
    if not isinstance(data, dict):
        return _empty_parse()
    result = _empty_parse(is_parsed=True)
    limits = _keep_limits()
    if is_start:
        limits["learnings"] = START_LEARNINGS_KEEP
    for pot in POTS:
        known = {c["cid"] for c in pools.get(pot) or []}
        entries = data.get(pot)
        if not isinstance(entries, list):
            continue
        taken = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            cid, why = entry.get("cid"), entry.get("why")
            if not isinstance(cid, str) or cid not in known or cid in taken:
                continue
            if not isinstance(why, str) or not why.strip():
                continue
            taken.add(cid)
            result["kept"][pot].append({"cid": cid, "why": why.strip()[:_WHY_CAP]})
            if len(result["kept"][pot]) >= limits[pot]:
                break
    clarify = data.get("clarify")
    if isinstance(clarify, str):
        result["clarify"] = _SPACE_RE.sub(" ", clarify).strip()[:_CLARIFY_CAP]
    return result
