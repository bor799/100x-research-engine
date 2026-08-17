"""Single-daemon entry: argument surface and one-pass guard behaviour."""

from __future__ import annotations

import pytest

import knowledge_extractor_v3.daemon as daemon_mod


def test_daemon_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as exc:
        daemon_mod.main(["--help"])
    assert exc.value.code == 0
    assert "scan" in capsys.readouterr().out


def test_daemon_run_once_propagates_guard_error(monkeypatch):
    from knowledge_extractor_v3.runtime_guard import RuntimeGuardError

    def _boom(project_root):
        raise RuntimeGuardError("state root rejected")

    monkeypatch.setattr(daemon_mod, "_setup", _boom)
    assert daemon_mod.main([]) == 1  # single pass surfaces guard failure


def test_daemon_loop_schedules_first_scan_immediately(monkeypatch):
    calls: list[bool] = []

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        calls.append(scan)
        raise KeyboardInterrupt  # exit loop after first iteration

    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    assert daemon_mod.main(["--loop", "--poll", "1", "--max-iter", "1"]) == 0
    assert calls == [True]
