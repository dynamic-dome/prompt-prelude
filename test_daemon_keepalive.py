# -*- coding: utf-8 -*-
"""Idee 8 (NOTES Befund 12): Daemon warm halten. 21 % der /classify-Calls
liefen ins 0,5-s-Timeout, nach >= 1 h Leerlauf 59 %, bei < 10 min Abstand nur
~7 %. Ein 5-Minuten-Ping hält den Embedder im Speicher. Fail-soft, stdlib,
Log-Pfad lazy + Env-Override (Hooks-bau-Regel)."""
import json

from tools import daemon_keepalive as ka


class TestPing:
    def test_success_reports_latency(self):
        clock = iter([10.0, 10.042])
        ok, ms, err = ka.ping(http_fn=lambda url, body, timeout: {"results": []},
                              clock=lambda: next(clock))
        assert ok is True and err is None and ms == 42.0

    def test_exercises_the_embedder_via_search(self):
        seen = {}

        def fn(url, body, timeout):
            seen.update(url=url, body=body, timeout=timeout)
            return {"results": []}

        ka.ping(http_fn=fn)
        assert seen["url"].endswith("/search")
        assert seen["body"]["query"].strip() and seen["body"]["k"] == 1
        assert seen["timeout"] == ka.PING_TIMEOUT_S

    def test_failure_is_reported_not_raised(self):
        def boom(url, body, timeout):
            raise ConnectionError("refused")

        ok, ms, err = ka.ping(http_fn=boom)
        assert ok is False and "ConnectionError" in err and ms >= 0


class TestLog:
    def test_log_path_follows_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRELUDE_KEEPALIVE_LOG", str(tmp_path / "k.jsonl"))
        assert ka.log_path() == str(tmp_path / "k.jsonl")

    def test_main_appends_one_record_and_exits_zero(self, monkeypatch, tmp_path):
        log = tmp_path / "k.jsonl"
        monkeypatch.setenv("PRELUDE_KEEPALIVE_LOG", str(log))
        monkeypatch.setattr(ka, "ping", lambda **kw: (True, 55.5, None))
        assert ka.main(now=lambda: 1000.0) == 0
        rec = json.loads(log.read_text(encoding="utf-8"))
        assert rec == {"t": 1000.0, "ok": True, "ms": 55.5}

    def test_main_never_raises(self, monkeypatch, tmp_path):
        monkeypatch.setenv("PRELUDE_KEEPALIVE_LOG", str(tmp_path / "no" / "such" / "dir" / "k"))

        def explode(**kw):
            raise RuntimeError("x")

        monkeypatch.setattr(ka, "ping", explode)
        assert ka.main() == 0

    def test_rotates_when_too_big(self, monkeypatch, tmp_path):
        log = tmp_path / "k.jsonl"
        log.write_text("x" * 50, encoding="utf-8")
        monkeypatch.setenv("PRELUDE_KEEPALIVE_LOG", str(log))
        monkeypatch.setattr(ka, "MAX_LOG_BYTES", 10)
        monkeypatch.setattr(ka, "ping", lambda **kw: (True, 1.0, None))
        ka.main(now=lambda: 1.0)
        assert (tmp_path / "k.jsonl.1").read_text(encoding="utf-8") == "x" * 50
        assert json.loads(log.read_text(encoding="utf-8"))["ms"] == 1.0
