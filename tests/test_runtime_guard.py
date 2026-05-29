import sqlite3
from pathlib import Path

import pytest

from knowledge_extractor_v3.runtime_guard import RuntimeGuard, RuntimeGuardError, RuntimePaths


def _paths(tmp_path: Path, **overrides) -> RuntimePaths:
    project_root = overrides.pop("project_root", tmp_path / "knowledge-extractor" / "v3")
    home = overrides.pop("home", tmp_path / "home")
    state_root = overrides.pop("state_root", home / ".100x_v3")
    queue_db_path = overrides.pop("queue_db_path", state_root / "queue.db")
    log_path = overrides.pop("log_path", home / "100x-v3-daemon.log")
    config_path = overrides.pop("config_path", project_root / "config" / "config.local.yaml")

    project_root.mkdir(parents=True, exist_ok=True)
    home.mkdir(parents=True, exist_ok=True)
    state_root.mkdir(parents=True, exist_ok=True)

    if overrides:
        raise AssertionError(f"Unexpected overrides: {overrides}")

    return RuntimePaths(
        project_root=project_root,
        state_root=state_root,
        queue_db_path=queue_db_path,
        log_path=log_path,
        config_path=config_path,
        home=home,
    )


def test_runtime_guard_accepts_temporary_v3_state_root(tmp_path):
    paths = _paths(tmp_path)

    fingerprint = RuntimeGuard(paths).validate()

    assert fingerprint.project_root.endswith("/v3")
    assert fingerprint.queue_db_path == str(paths.queue_db_path)
    assert fingerprint.state_root == str(paths.state_root)


def test_runtime_guard_accepts_public_repo_checkout_name(tmp_path):
    paths = _paths(tmp_path, project_root=tmp_path / "information-tracking-agent")

    fingerprint = RuntimeGuard(paths).validate()

    assert fingerprint.project_root.endswith("/information-tracking-agent")


def test_runtime_guard_rejects_v2_queue_path(tmp_path):
    home = tmp_path / "home"
    state_root = home / ".100x_v2"
    paths = _paths(tmp_path, home=home, state_root=state_root, queue_db_path=state_root / "queue.db")

    with pytest.raises(RuntimeGuardError, match="V2"):
        RuntimeGuard(paths).validate()


def test_runtime_guard_rejects_project_root_pointing_at_v2(tmp_path):
    paths = _paths(tmp_path, project_root=tmp_path / "knowledge-extractor" / "v2")

    with pytest.raises(RuntimeGuardError):
        RuntimeGuard(paths).validate()


def test_runtime_guard_rejects_shadow_home(tmp_path):
    home = tmp_path / "codepilot-shadow-abc"
    state_root = home / ".100x_v3"
    paths = _paths(tmp_path, home=home, state_root=state_root, queue_db_path=state_root / "queue.db")

    with pytest.raises(RuntimeGuardError, match="shadow HOME"):
        RuntimeGuard(paths).validate()


def test_runtime_guard_rejects_existing_queue_with_missing_schema_columns(tmp_path):
    paths = _paths(tmp_path)
    with sqlite3.connect(paths.queue_db_path) as conn:
        conn.execute(
            """
            CREATE TABLE queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                status TEXT NOT NULL
            )
            """
        )
        conn.commit()

    with pytest.raises(RuntimeGuardError, match="not V3-compatible"):
        RuntimeGuard(paths).validate()
