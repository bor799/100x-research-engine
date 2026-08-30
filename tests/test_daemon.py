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

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
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

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
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

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
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

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
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


def test_singleton_lock_contention_and_release(tmp_path, monkeypatch):
    import os

    db = tmp_path / "queue.db"
    db.write_text("", encoding="utf-8")

    class _Loader(_StubLoader):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self):
            from knowledge_extractor_v3.config_loader import V3Config

            return V3Config()

        def expand_path(self, value) -> Path:
            return db  # every path resolves to the isolated database

    monkeypatch.setattr(daemon_mod, "ConfigLoader", _Loader)

    fd = daemon_mod._acquire_singleton_lock()
    assert isinstance(fd, int)
    try:
        with pytest.raises(daemon_mod.SingletonContended):
            daemon_mod._acquire_singleton_lock()  # second daemon loses the race
    finally:
        os.close(fd)
    reacquired = daemon_mod._acquire_singleton_lock()  # released -> reacquirable
    assert isinstance(reacquired, int)
    os.close(reacquired)
    # The lock is a sidecar file, never the database itself (an flock on the
    # db file collides with SQLite's fcntl locks on macOS).
    assert (tmp_path / "queue.db.loop.lock").exists()
    assert db.read_text(encoding="utf-8") == ""  # database untouched


def test_singleton_lock_sidecar_never_blocks_sqlite(tmp_path):
    """The 2026-08-30 production deadlock: flock(queue.db) made the daemon's
    own SQLite writes fail. The sidecar lock must leave sqlite free."""
    import fcntl
    import os
    import sqlite3

    db = tmp_path / "queue.db"
    lock = tmp_path / (db.name + ".loop.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with sqlite3.connect(db, timeout=2) as conn:  # short fuse: must not need it
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
            assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1
    finally:
        os.close(fd)


def test_daemon_exits_when_singleton_contended(monkeypatch, capsys):
    def _contended():
        raise daemon_mod.SingletonContended("/tmp/queue.db")

    def _must_not_run(**kwargs):
        raise AssertionError("a contended daemon must never reach a batch")

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", _contended)
    monkeypatch.setattr(daemon_mod, "run_once", _must_not_run)
    assert daemon_mod.main(["--loop", "--max-iter", "1"]) == 0
    assert "already holds" in capsys.readouterr().err


def test_daemon_exits_cleanly_on_sigterm_after_batch(monkeypatch, capsys):
    import signal

    calls: list[int] = []

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        calls.append(1)
        daemon_mod._handle_shutdown(signal.SIGTERM, None)  # TERM lands mid-loop
        return 0

    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    assert daemon_mod.main(["--loop", "--poll", "1", "--max-iter", "5"]) == 0
    assert len(calls) == 1  # finished the current batch, then exited
    assert "shutdown requested" in capsys.readouterr().out


def test_magazine_bind_failure_retries_instead_of_disabling(monkeypatch, tmp_path):
    """A port held by a stale process must cost one 300s backoff, not the
    service's whole lifetime (the old `magazine_server = False` behaviour)."""
    import dataclasses

    from knowledge_extractor_v3.config_loader import V3Config

    config = V3Config()
    outputs = dataclasses.replace(
        config.outputs,
        magazine_enabled=True,
        obsidian_root=str(tmp_path / "vault"),
    )
    config = dataclasses.replace(config, outputs=outputs)

    class _Loader(_StubLoader):
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load(self):
            return config

        def expand_path(self, value) -> Path:
            return tmp_path / "vault"

    class _FakeServer:
        built: list["_FakeServer"] = []

        def __init__(self, root, port=0, reviewer=None) -> None:
            self.port = port
            self.starts = 0
            self.closes = 0
            type(self).built.append(self)

        def start(self) -> None:
            self.starts += 1
            if len(type(self).built) == 1:
                raise OSError("port 8765 already bound")

        def close(self) -> None:
            self.closes += 1

    import knowledge_extractor_v3.magazine as magazine_mod

    clock = _StubClock(1000.0)

    def _fake_run_once(*, scan: bool = True, batch_size: int = 10) -> int:
        clock.now += 200.0
        return 0

    monkeypatch.setattr(daemon_mod, "ConfigLoader", _Loader)
    monkeypatch.setattr(daemon_mod, "_acquire_singleton_lock", lambda: None)
    monkeypatch.setattr(daemon_mod, "time", clock)
    monkeypatch.setattr(daemon_mod, "run_once", _fake_run_once)
    monkeypatch.setattr(magazine_mod, "MagazineServer", _FakeServer)
    monkeypatch.setattr(magazine_mod, "build_issue", lambda root, week=None: None)
    monkeypatch.setattr(magazine_mod, "build_reviewer", lambda config, root: None)

    assert daemon_mod.main(["--loop", "--no-dedup", "--poll", "1", "--max-iter", "4"]) == 0

    # Iteration 1: bind fails. Iteration 2: still inside the 300s backoff
    # (t=1200 < 1300). Iteration 3: t=1400 — retry succeeds. Iteration 4: exit.
    assert len(_FakeServer.built) == 2
    assert _FakeServer.built[0].starts == 1  # failed attempt
    assert _FakeServer.built[1].starts == 1  # successful retry
    assert _FakeServer.built[1].closes == 1  # closed on daemon exit
