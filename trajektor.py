"""trajektor.py — PostToolUse-Drift-Beobachter (T-12, Spec 2026-07-19).

Schwester-Hook von prompt_prelude: misst Drift des Tool-Call-Stroms gegen den
letzten Arbeits-Prompt (Goal-Anchor) deterministisch — keine Daemon-Calls,
fail-soft, Exit immer 0."""
import json
import os
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
    return {"calls": [], "call_count": 0, "armed": True, "cooldown_until": 0, "fires": 0}


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


# Pfadartig innerhalb von Bash-Kommandos: mindestens ein Separator
_BASH_PATH_RE = re.compile(r"[\w.~-]+(?:[\\/][\w.~-]+)+")


def _norm(p):
    return str(p).replace("\\", "/").lower()


def extract_tool_paths(tool_name, tool_input):
    """Berührte Pfade aus tool_input — fail-soft, fehlende Felder egal."""
    if not isinstance(tool_input, dict):
        return []
    paths = []
    for field in ("file_path", "path", "notebook_path"):
        v = tool_input.get(field)
        if isinstance(v, str) and v.strip():
            paths.append(_norm(v.strip()))
    cmd = tool_input.get("command")
    if isinstance(cmd, str):
        paths.extend(_norm(m) for m in _BASH_PATH_RE.findall(cmd))
    # Dedupe bei stabiler Reihenfolge
    seen, out = set(), []
    for p in paths:
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def update_window(window, tool_name, paths):
    calls = window["calls"] + [{"tool": tool_name, "paths": paths}]
    return {**window, "calls": calls[-WINDOW_K:],
            "call_count": window["call_count"] + 1}


def main():
    try:
        raw = _read_stdin_utf8()
        try:
            payload = json.loads(raw or "{}")
        except Exception:
            return 0
        if not isinstance(payload, dict):
            return 0
        session_id = payload.get("session_id", "default") or "default"
        state_dir = _default_state_dir()
        cleanup_state(state_dir, time.time())
        window = load_window(session_id, state_dir)
        paths = extract_tool_paths(payload.get("tool_name", ""),
                                   payload.get("tool_input"))
        window = update_window(window, str(payload.get("tool_name", "")), paths)
        save_window(session_id, state_dir, window)
        # Score/Fire folgt in Task 5 — bis dahin stiller Beobachter.
    except Exception:
        pass
    return 0


def _default_state_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), ".dedupe")


if __name__ == "__main__":
    sys.exit(main())
