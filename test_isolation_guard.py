"""Regressionsschutz Test-Isolation (Lagebericht 2026-09-25).

Befund: test_trajektor.py::TestE2E startete trajektor.py als Subprozess ohne
Override. Die conftest-Monkeypatches greifen dort nicht, trajektor.py kannte keinen
Env-Override, und jeder Suite-Lauf schrieb eine Zeile in die echte trajektor.jsonl
(23 Testzeilen gefunden) plus eine Datei in .dedupe/. Geprueft werden die real
aufgeloesten Pfade gegen die echten Zieldateien.
"""
import os

import prompt_prelude as pp
import trajektor as tj

ROOT = os.path.dirname(os.path.abspath(__file__))
_REAL_FILES = {os.path.normcase(os.path.join(ROOT, n))
               for n in ("trajektor.jsonl", "prompt_prelude.jsonl", "prelude_decisions.jsonl")}
_REAL_STATE = os.path.normcase(os.path.join(ROOT, ".dedupe"))


def _is_real(path) -> bool:
    p = os.path.normcase(os.path.abspath(path))
    return p in _REAL_FILES or p == _REAL_STATE or p.startswith(_REAL_STATE + os.sep)


def test_trajektor_defaults_are_isolated():
    assert not _is_real(tj._default_traj_log()), tj._default_traj_log()
    assert not _is_real(tj._default_state_dir()), tj._default_state_dir()


def test_prelude_defaults_are_isolated():
    for fn in (pp._default_log_path, pp._default_decision_log_path, pp._default_state_dir):
        assert not _is_real(fn()), f"{fn.__name__} -> {fn()}"


def test_trajektor_paths_follow_env(monkeypatch, tmp_path):
    # Subprozesse erben os.environ: nur ein Env-Override erreicht das Kind.
    monkeypatch.setenv("TRAJEKTOR_LOG", str(tmp_path / "x.jsonl"))
    monkeypatch.setenv("TRAJEKTOR_STATE_DIR", str(tmp_path / "st"))
    assert tj._default_traj_log() == str(tmp_path / "x.jsonl")
    assert tj._default_state_dir() == str(tmp_path / "st")
