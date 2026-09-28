# -*- coding: utf-8 -*-
"""Atlas-Daemon warm halten (Idee 8, NOTES Befund 12).

Nach Leerlauf antwortet der Daemon zu langsam: 21 % der Prelude-/classify-
Calls liefen ins 0,5-s-Timeout, nach >= 1 h Pause 59 %, bei < 10 min Abstand
nur ~7 %. Dann gilt der Daemon als down und die Prelude fällt auf die
schwächere SQLite-Suche zurück — ausgerechnet beim ersten Prompt nach einer
Pause. Ein Mini-/search alle 5 Minuten (Scheduled Task, pythonw) hält den
Embedder im Speicher. Jeder Ping wird mit Latenz geloggt: das Log zeigt, ob
der Daemon warm bleibt, und liefert erstmals echte Kaltstart-Latenzen.

Eigenständig (stdlib, kein Import aus prompt_prelude): der Task startet die
Datei direkt per Pfad. Fail-soft: Exit immer 0.
"""
import json
import os
import sys
import time
import urllib.request

DAEMON_URL_DEFAULT = "http://127.0.0.1:7801"
PING_TIMEOUT_S = 10.0          # großzügig: wir wollen die echte Latenz sehen
PING_QUERY = "prompt prelude keepalive"
MAX_LOG_BYTES = 2_000_000


def log_path():
    return os.environ.get("PRELUDE_KEEPALIVE_LOG") or os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "keepalive.jsonl")


def _post_json(url, body, timeout):
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ping(http_fn=None, clock=time.perf_counter):
    """-> (ok, latency_ms, error|None). /search statt /health: nur ein echter
    Embedding-Call hält die Modellgewichte im Arbeitsspeicher."""
    fn = http_fn or _post_json
    url = os.environ.get("ATLAS_DAEMON_URL", DAEMON_URL_DEFAULT).rstrip("/") + "/search"
    start = clock()
    try:
        fn(url, {"query": PING_QUERY, "k": 1}, PING_TIMEOUT_S)
        return True, round((clock() - start) * 1000, 1), None
    except Exception as exc:
        return False, round((clock() - start) * 1000, 1), f"{type(exc).__name__}: {exc}"[:200]


def main(now=time.time):
    try:
        ok, ms, err = ping()
        rec = {"t": now(), "ok": ok, "ms": ms}
        if err:
            rec["error"] = err
        path = log_path()
        if os.path.exists(path) and os.path.getsize(path) > MAX_LOG_BYTES:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
