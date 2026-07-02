import sqlite3
import pytest

import prompt_prelude as _pp


@pytest.fixture(autouse=True)
def no_real_daemon(monkeypatch):
    """Kein Test darf je den echten Atlas-Daemon (127.0.0.1:7801) treffen —
    der läuft parallel evtl. (nicht) und würde die Suite nichtdeterministisch
    machen. Default: Daemon 'down' (ConnectionError -> Fallback-Pfade).
    Tests, die Daemon-Verhalten brauchen, injizieren ein explizites http_fn."""
    def _refuse(url, body, timeout):
        raise ConnectionError("no daemon in tests")
    monkeypatch.setattr(_pp, "_http_post_json", _refuse)


@pytest.fixture
def tmp_state_dir(tmp_path):
    """Isolierter Pfad für Telemetrie + Dedupe (nie echte State-Dateien in Tests)."""
    d = tmp_path / "state"
    d.mkdir()
    return str(d)


@pytest.fixture
def fake_atlas_db(tmp_path):
    """Kontrollierter FTS5-Mini-Index, deterministisch."""
    db = tmp_path / "bm25.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(text, record_id, chunk_id, source_path)")
    rows = [
        ("systematic debugging skill for bugs and test failures", "skill:diagnose-hitl", "0", "x.md"),
        ("frontend design ui component layout skill", "skill:frontend-design", "0", "y.md"),
        ("d3js data visualization chart skill", "skill:d3js-visualization", "0", "z.md"),
    ]
    conn.executemany("INSERT INTO chunks VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(db)
