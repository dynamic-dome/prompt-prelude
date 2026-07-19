"""trajektor.py — PostToolUse-Drift-Beobachter (T-12, Spec 2026-07-19).

Schwester-Hook von prompt_prelude: misst Drift des Tool-Call-Stroms gegen den
letzten Arbeits-Prompt (Goal-Anchor) deterministisch — keine Daemon-Calls,
fail-soft, Exit immer 0."""
import json
import os
import posixpath
import re
import sys
import time

from prompt_prelude import (_read_stdin_utf8, _safe_session, cleanup_state,
                            significant_tokens)

WINDOW_K = 15


def load_anchor(session_id, state_dir):
    try:
        from prompt_prelude import anchor_path
        with open(anchor_path(session_id, state_dir), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def window_path(session_id, state_dir):
    return os.path.join(state_dir, f"trajwin_{_safe_session(session_id)}.json")


def _default_window():
    return {"calls": [], "call_count": 0, "armed": True, "cooldown_until": 0,
             "fires": 0, "anchor_t": 0}


def load_window(session_id, state_dir):
    try:
        with open(window_path(session_id, state_dir), encoding="utf-8") as f:
            w = json.load(f)
        if not isinstance(w, dict) or not isinstance(w.get("calls"), list):
            return _default_window()
        for key, default in _default_window().items():
            w.setdefault(key, default)
        return w
    except Exception:
        return _default_window()


def save_window(session_id, state_dir, window):
    try:
        os.makedirs(state_dir, exist_ok=True)
        p = window_path(session_id, state_dir)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(window, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception:
        pass


# Pfadartig innerhalb von Bash-Kommandos: mindestens ein Separator.
# Optionaler Windows-Laufwerksbuchstabe vorweg (z.B. "C:\repo\tests\test_x.py")
# -- das erste Segment ist bewusst [\w.~-]* (nicht +), sonst frisst das
# Backtracking den Drive-Prefix wieder weg, weil direkt nach "C:" ein
# Separator folgt und kein Wortzeichen (Codex-Verifier-Finding).
_BASH_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\w.~-]*(?:[\\/][\w.~-]+)+")


def _norm(p):
    s = str(p).replace("\\", "/").lower()
    if not s:
        return s
    # posixpath.normpath loest ".."/"." auf, sonst gilt ".../a/../b" faelschlich
    # als Unterpfad von ".../a" (Codex-Finding 2). normpath("") == "." waere
    # eine falsche Ueberraschung fuer einen leeren Input -> davor abgefangen;
    # normpath ist sonst fail-soft (wirft nie).
    return posixpath.normpath(s)


_QUOTED_RE = re.compile(r'"([^"]+)"|\'([^\']+)\'')


def extract_tool_paths(tool_name, tool_input):
    """Berührte Pfade aus tool_input — fail-soft, fehlende Felder egal.

    Gequotete Bash-Pfade mit Leerzeichen ("C:\\repo space\\x.py") werden vor
    dem Regex-Scan als Ganzes herausgezogen (Codex-Finding 3), sonst zerlegt
    _BASH_PATH_RE sie am Leerzeichen in Fragmente. Ungequotete Pfade mit
    Leerzeichen bleiben bewusst unerkannt -- die sind auch fuer eine echte
    Shell mehrdeutig (welches Token gehoert noch zum Pfad?).
    """
    if not isinstance(tool_input, dict):
        return []
    paths = []
    for field in ("file_path", "path", "notebook_path"):
        v = tool_input.get(field)
        if isinstance(v, str) and v.strip():
            paths.append(_norm(v.strip()))
    cmd = tool_input.get("command")
    if isinstance(cmd, str):
        rest_parts = []
        last_end = 0
        for m in _QUOTED_RE.finditer(cmd):
            inner = m.group(1) if m.group(1) is not None else m.group(2)
            rest_parts.append(cmd[last_end:m.start()])
            last_end = m.end()
            if re.search(r"[\\/]", inner):
                paths.append(_norm(inner))
        rest_parts.append(cmd[last_end:])
        rest = " ".join(rest_parts)
        paths.extend(_norm(m) for m in _BASH_PATH_RE.findall(rest))
    # Dedupe bei stabiler Reihenfolge
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def update_window(window, tool_name, paths, is_test=False):
    calls = window["calls"] + [{"tool": tool_name, "paths": paths, "test": is_test}]
    return {**window, "calls": calls[-WINDOW_K:],
            "call_count": window["call_count"] + 1}


W_TOKEN, W_PATH, W_PHASE = 0.5, 0.3, 0.2

_EXPLORE_TOOLS = {"Read", "Grep", "Glob"}
_BUILD_TOOLS = {"Edit", "Write", "NotebookEdit"}
_TEST_MARKERS = ("pytest", "test", "npm run", "cargo test", "go test")


def is_test_command(tool_name, tool_input):
    """True, wenn der Call ein Test-Runner ist -- unabhaengig davon, ob er
    Pfad-Argumente hat ("pytest -q" liefert extract_tool_paths()==[])."""
    if tool_name != "Bash" or not isinstance(tool_input, dict):
        return False
    cmd = tool_input.get("command")
    if not isinstance(cmd, str):
        return False
    low = cmd.lower()
    return any(m in low for m in _TEST_MARKERS)


def window_tokens(window):
    toks = set()
    for call in window["calls"]:
        for p in call["paths"]:
            toks |= significant_tokens(p.replace("/", " ").replace(".", " ").replace("_", " ").replace("-", " "))
    return toks


def phase_from_tools(window):
    tools = [c["tool"] for c in window["calls"]]
    if not tools:
        return "mixed"
    n = len(tools)
    explore = sum(1 for t in tools if t in _EXPLORE_TOOLS)
    build = sum(1 for t in tools if t in _BUILD_TOOLS)
    verify = sum(1 for c in window["calls"] if c.get("test"))
    if verify >= max(1, n // 2):
        return "verify"
    if build > n * 0.6:
        return "build"
    if explore > n * 0.6:
        return "explore"
    return "mixed"


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def drift_score(anchor, window):
    """Deterministischer 3-Komponenten-Drift-Score in [0,1]."""
    if not window["calls"]:
        return {"total": 0.0, "token_shift": 0.0, "path_divergence": 0.0,
                "phase_flip": 0.0, "window_phase": "mixed"}
    a_toks = set(anchor.get("tokens") or [])
    w_toks = window_tokens(window)
    token_shift = 1.0 - _jaccard(a_toks, w_toks) if a_toks and w_toks else (1.0 if a_toks else 0.0)

    a_dirs = [d for d in (anchor.get("dirs") or [])]
    all_paths = [p for c in window["calls"] for p in c["paths"]]
    if a_dirs and all_paths:
        # Segment-Grenze statt Substring: "auth-old" ist kein Unterverzeichnis
        # von "auth" (Codex-Verifier-Finding).
        outside = sum(1 for p in all_paths
                      if not any(p == d or p.startswith(d + "/") for d in a_dirs))
        path_divergence = outside / len(all_paths)
    else:
        path_divergence = 0.0  # ohne Anchor-Pfade kein Urteil (neutral, kein Fehlalarm)

    window_phase = phase_from_tools(window)
    anchor_phase = anchor.get("phase") or "quiet"
    # planning-Anchor + reine build-Kette = klarer Phasenwechsel; quiet urteilt nicht
    phase_flip = 1.0 if (anchor_phase == "planning" and window_phase == "build") else 0.0

    total = W_TOKEN * token_shift + W_PATH * path_divergence + W_PHASE * phase_flip
    return {"total": round(total, 4), "token_shift": round(token_shift, 4),
            "path_divergence": round(path_divergence, 4),
            "phase_flip": phase_flip, "window_phase": window_phase}


TRAJ_FIRE = 0.65
TRAJ_CLEAR = 0.45
COOLDOWN_CALLS = 10
SESSION_FIRE_CAP = 3


def decide(window, total):
    """Hysterese + Cooldown + Session-Cap. Gibt (status, updated_window) zurück.

    Reihenfolge der Gates ist Absicht: cap vor cooldown vor hysterese —
    Telemetrie soll den bindendsten Grund nennen."""
    w = dict(window)
    if total <= TRAJ_CLEAR and not w["armed"]:
        w["armed"] = True          # Score beruhigt -> wieder scharf
    if total < TRAJ_FIRE:
        return "below", w
    if w["fires"] >= SESSION_FIRE_CAP:
        return "cap", w
    if w["call_count"] < w["cooldown_until"]:
        return "cooldown", w
    if not w["armed"]:
        return "not_armed", w
    w["armed"] = False
    w["cooldown_until"] = w["call_count"] + COOLDOWN_CALLS
    w["fires"] += 1
    return "fire", w


TRAJ_SCHEMA = "t1"  # Ära t1 — nie mit prompt_prelude.jsonl-Ären mischen (D7)


def log_traj(record, log_path):
    try:
        record.setdefault("tv", TRAJ_SCHEMA)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def build_reframing(anchor, score):
    verdict = "Drift" if score["total"] >= TRAJ_FIRE else "Nebenpfad"
    tokens = list(anchor.get("tokens") or [])[:3]
    dirs = list(anchor.get("dirs") or [])[:2]
    kernpunkte = "Kernpunkte des Auftrags: " + (", ".join(tokens) if tokens else "?")
    if dirs:
        kernpunkte += " · Pfade: " + ", ".join(dirs)
    lines = [
        f"TRAJEKTOR (Drift-Check, deterministisch): Ursprungs-Auftrag war: "
        f"„{anchor.get('prompt_preview', '?')}“",
        f"Der aktuelle Tool-Pfad (Phase {score['window_phase']}) bewertet sich "
        f"dagegen als {verdict} (Score {score['total']}, "
        f"token_shift {score['token_shift']}, path_divergence {score['path_divergence']}).",
        "Kurz prüfen: dient die aktuelle Arbeit noch diesem Auftrag, oder ist es "
        "ein unbeauftragter Nebenpfad? Wenn Nebenpfad: benennen oder zurückkehren.",
        kernpunkte,
    ]
    return "\n".join(lines)


def make_ptu_output(additional_context, system_message):
    if not additional_context and not system_message:
        return ""
    out = {}
    if additional_context:
        out["hookSpecificOutput"] = {
            "hookEventName": "PostToolUse",
            "additionalContext": additional_context,
        }
    if system_message:
        out["systemMessage"] = system_message
    return json.dumps(out, ensure_ascii=False)


def _default_traj_log():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "trajektor.jsonl")


def run_traj(payload, *, state_dir, log_path, now):
    if not isinstance(payload, dict):
        return ""
    session_id = payload.get("session_id", "default") or "default"
    tool_name = str(payload.get("tool_name", ""))
    window = load_window(session_id, state_dir)
    tool_input = payload.get("tool_input")
    paths = extract_tool_paths(tool_name, tool_input)
    is_test = is_test_command(tool_name, tool_input)
    window = update_window(window, tool_name, paths, is_test=is_test)

    anchor = load_anchor(session_id, state_dir)
    if not anchor:
        save_window(session_id, state_dir, window)
        log_traj({"t": now, "session": session_id, "status": "no_anchor",
                  "tool": tool_name}, log_path)
        return ""

    if window.get("anchor_t") != anchor.get("t"):
        # Re-Anchor (neuer User-Prompt): das Fenster gehoert dem alten Thema
        # -> Aera wechseln, sonst feuert der erste Call nach legitimem
        # Themenwechsel faelschlich (Codex-Verifier-Finding). Session-Cap/
        # Cooldown/fires sind Session-Semantik und bleiben unberuehrt.
        window = {**window, "calls": window["calls"][-1:], "armed": True,
                  "anchor_t": anchor.get("t")}

    score = drift_score(anchor, window)
    status, window = decide(window, score["total"])
    save_window(session_id, state_dir, window)
    rec = {"t": now, "session": session_id, "status": status, "tool": tool_name,
           "score": score, "call_count": window["call_count"],
           "fires": window["fires"]}
    log_traj(rec, log_path)
    if status != "fire":
        return ""
    ctx = build_reframing(anchor, score)
    msg = (f"trajektor ▸ drift · score={score['total']} · "
           f"fire {window['fires']}/{SESSION_FIRE_CAP}")
    return make_ptu_output(ctx, msg)


def main():
    try:
        raw = _read_stdin_utf8()
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            return 0
        state_dir = _default_state_dir()
        cleanup_state(state_dir, time.time())
        try:
            out = run_traj(payload, state_dir=state_dir,
                           log_path=_default_traj_log(), now=time.time())
            if out:
                print(out)
        except Exception as e:
            log_traj({"t": time.time(), "status": "crash",
                      "error": str(e)[:200]}, _default_traj_log())
    except Exception:
        pass
    return 0


def _default_state_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe")


if __name__ == "__main__":
    sys.exit(main())
