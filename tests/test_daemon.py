"""Single-daemon entry: argument surface, one-pass guard, dedup gate."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

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


class _StubClock:
    """Replaces daemon_mod.time so tests control the dedup gate without
    touching the real vault via the magazine bootstrap config."""

    def __init__(self, start: float) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        return None


class _StubLoader:
    """ConfigLoader stand-in whose config never enables the magazine service,
    keeping loop tests hermetic (no real vault, no port bind)."""

    def __init__(self, *args, **kwargs) -> None:
        from knowledge_extractor_v3.config_loader import V3Config

        self._config = V3Config()

    def load(self):
        return self._config

    def expand_path(self, value) -> Path:
        return Path(str(value))


def test_daemon_loop_dedup_gate_respects_startup_grace(monkeypatch):
    fired: list[float] = []
    clock = _StubClock(1000.0)

    def _fake_dedup() -> float:
        fired.append(clock.now)
        return 60.0

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        raise KeyboardInterrupt

    monkeypatch.setattr(daemon_mod, "time", clock)
    monkeypatch.setattr(daemon_mod, "ConfigLoader", _StubLoader)
    monkeypatch.setattr(daemon_mod, "_run_periodic_dedup", _fake_dedup)
    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    assert daemon_mod.main(["--loop", "--poll", "1", "--max-iter", "1"]) == 0
    assert fired == []  # t=0 is inside the 5-minute grace, never fires


def test_daemon_loop_dedup_gate_fires_after_grace_and_reschedules(monkeypatch):
    fired: list[float] = []
    clock = _StubClock(1000.0)

    def _fake_dedup() -> float:
        fired.append(clock.now)
        return 60.0

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        clock.now += 400.0  # each worker batch takes longer than the grace
        return 0

    monkeypatch.setattr(daemon_mod, "time", clock)
    monkeypatch.setattr(daemon_mod, "ConfigLoader", _StubLoader)
    monkeypatch.setattr(daemon_mod, "_run_periodic_dedup", _fake_dedup)
    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    assert daemon_mod.main(["--loop", "--poll", "1", "--max-iter", "3"]) == 0
    # The grace is checked after the batch: iteration 1 starts at t=1000 but
    # fires at 1400 (past the 1300 grace); each later iteration refires on its
    # own 60s schedule.
    assert fired == [1400.0, 1800.0, 2200.0]


def test_daemon_no_dedup_flag_disables_gate(monkeypatch):
    fired: list[float] = []
    clock = _StubClock(1000.0)

    def _fake_dedup() -> float:
        fired.append(clock.now)
        return 60.0

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        clock.now += 400.0
        return 0

    monkeypatch.setattr(daemon_mod, "time", clock)
    monkeypatch.setattr(daemon_mod, "ConfigLoader", _StubLoader)
    monkeypatch.setattr(daemon_mod, "_run_periodic_dedup", _fake_dedup)
    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    assert daemon_mod.main(["--loop", "--no-dedup", "--poll", "1", "--max-iter", "3"]) == 0
    assert fired == []


def test_periodic_dedup_swallows_config_failure_and_returns_interval(monkeypatch):
    class _BoomLoader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self):
            raise RuntimeError("config gone")

    monkeypatch.setattr(daemon_mod, "ConfigLoader", _BoomLoader)
    assert daemon_mod._run_periodic_dedup() == 24 * 3600.0  # never raises


def test_periodic_dedup_honours_configured_interval(monkeypatch):
    import dataclasses

    from knowledge_extractor_v3.config_loader import DedupConfig, V3Config

    config = dataclasses.replace(
        V3Config(), dedup=DedupConfig(enabled=True, dedup_interval_hours=6)
    )
    config = dataclasses.replace(
        config,
        outputs=dataclasses.replace(config.outputs, obsidian_root="/tmp/does-not-matter"),
    )
    calls: list[Path] = []

    class _Loader(_StubLoader):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self):
            return config

        def expand_path(self, value) -> Path:
            return Path(str(value))

    def _fake_dedupe_vault(root):
        calls.append(root)
        from knowledge_extractor_v3.outputs.dedupe import DedupeReport

        return DedupeReport()

    import knowledge_extractor_v3.outputs.dedupe as dedupe_mod

    monkeypatch.setattr(daemon_mod, "ConfigLoader", _Loader)
    monkeypatch.setattr(dedupe_mod, "dedupe_vault", _fake_dedupe_vault)
    assert daemon_mod._run_periodic_dedup() == 6 * 3600.0
    assert calls == [Path("/tmp/does-not-matter")]
