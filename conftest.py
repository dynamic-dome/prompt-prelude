import os
import sqlite3
import pytest

import prompt_prelude as _pp
import trajektor as _tj

_ROOT = os.path.dirname(os.path.abspath(__file__))
_REAL_FILES = {os.path.normcase(os.path.join(_ROOT, n))
               for n in ("trajektor.jsonl", "prompt_prelude.jsonl", "prelude_decisions.jsonl")}
_REAL_STATE = os.path.normcase(os.path.join(_ROOT, ".dedupe"))


def _poison_guard(when):
    """Real aufgelöste Default-Pfade gegen die echten Zieldateien prüfen
    (Muster: wiki test-db-isolation, dual-bridge ff70df3)."""
    for fn in (_pp._default_log_path, _pp._default_decision_log_path, _pp._default_state_dir,
               _tj._default_traj_log, _tj._default_state_dir):
        p = os.path.normcase(os.path.abspath(fn()))
        if p in _REAL_FILES or p == _REAL_STATE or p.startswith(_REAL_STATE + os.sep):
            raise RuntimeError(f"REFUSING: {fn.__name__} zeigt {when} auf echte Daten: {p}")


@pytest.fixture(autouse=True)
def hermetic_default_logs(monkeypatch, tmp_path, tmp_path_factory):
    """Kein Test darf je in die echten Telemetrie-/Decision-Logs im Projektordner
    schreiben. run() fällt bei decision_log_path=None auf den Default zurück —
    genau so landete ein t=0-Testeintrag im echten prelude_decisions.jsonl
    (Befund 10, Nebenbefund). Defaults hart auf tmp umbiegen.
    Trajektor läuft in test_trajektor.py auch als Subprozess, den kein
    Monkeypatch erreicht: dort wirken nur die geerbten Env-Overrides."""
    monkeypatch.setattr(_pp, "_default_log_path",
                        lambda: str(tmp_path / "default_telemetry.jsonl"))
    monkeypatch.setattr(_pp, "_default_decision_log_path",
                        lambda: str(tmp_path / "default_decisions.jsonl"))
    iso = tmp_path_factory.mktemp("hermetic")
    monkeypatch.setattr(_pp, "_default_state_dir", lambda: str(iso / "prelude-state"))
    monkeypatch.setenv("TRAJEKTOR_LOG", str(iso / "trajektor.jsonl"))
    monkeypatch.setenv("TRAJEKTOR_STATE_DIR", str(iso / "trajektor-state"))
    # v10 Headless-Skip liest CLAUDE_CODE_ENTRYPOINT: läuft die Suite selbst in
    # einem `claude -p` (sdk-cli), würde sonst jeder run()-Test still skippen.
    # Deterministisch "interaktiv"; Headless-Tests setzen es explizit um.
    monkeypatch.setenv("CLAUDE_CODE_ENTRYPOINT", "cli")
    _poison_guard("vor dem Test")
    yield
    _poison_guard("nach dem Test")


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
        # record_ids spiegeln das Produktions-Schema: Capabilities tragen den
        # Präfix "atlas/" (v5-Gate filtert darauf), plus ein Nicht-Capability-Row,
        # der vom Filter zwingend verworfen werden muss.
        ("systematic debugging skill for bugs and test failures", "atlas/skill:diagnose-hitl", "0", "x.md"),
        ("frontend design ui component layout skill", "atlas/skill:frontend-design", "0", "y.md"),
        ("d3js data visualization chart skill", "atlas/skill:d3js-visualization", "0", "z.md"),
        ("frontend design ui component layout unrelated wiki copy", "haupt-wiki/queries/session-x", "0", "junk.md"),
    ]
    conn.executemany("INSERT INTO chunks VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return str(db)
