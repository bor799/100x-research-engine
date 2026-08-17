import json
from datetime import UTC, datetime
from pathlib import Path

from knowledge_extractor_v3.config_loader import RuntimeConfig, V3Config
from knowledge_extractor_v3.health import HealthChecker, HealthStatus
from knowledge_extractor_v3.absorption_prompt import load_absorption_prompt


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_worst_status_accepts_generators():
    status = HealthChecker._worst_status(
        item for item in [HealthStatus.HEALTHY, HealthStatus.ERROR]
    )

    assert status is HealthStatus.ERROR


def test_role_lock_exited_status_is_stale(tmp_path):
    role_dir = tmp_path / "roles"
    role_dir.mkdir()
    (role_dir / "worker-loop.json").write_text(
        json.dumps(
            {
                "role": "worker-loop",
                "status": "exited",
                "pid": 999999,
                "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    checker = HealthChecker(V3Config(runtime=RuntimeConfig(state_root=str(tmp_path))))

    check = checker._check_role_locks()

    assert check.status is HealthStatus.WARNING
    assert check.detail["stale_roles"] == ["worker-loop"]


def test_role_lock_stopped_status_is_not_stale(tmp_path):
    role_dir = tmp_path / "roles"
    role_dir.mkdir()
    (role_dir / "worker-loop.json").write_text(
        json.dumps(
            {
                "role": "worker-loop",
                "status": "stopped",
                "pid": 999999,
                "updated_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            }
        ),
        encoding="utf-8",
    )
    checker = HealthChecker(V3Config(runtime=RuntimeConfig(state_root=str(tmp_path))))

    check = checker._check_role_locks()

    assert check.status is HealthStatus.HEALTHY


def test_prompt_health_reports_absorption_prompt_and_hash():
    checker = HealthChecker(V3Config(), absorption_prompt=load_absorption_prompt(PROJECT_ROOT))

    check = checker._check_prompt_registry()

    assert check.status is HealthStatus.HEALTHY
    assert check.detail["active_bundle"] == "v4_absorption"
    assert check.detail["prompt_hash"]


def test_prompt_health_errors_when_prompt_file_is_missing(tmp_path):
    checker = HealthChecker(V3Config(), absorption_prompt=None)
    checker._prompts = None
    # Force the fallback loader onto an empty project root.
    import knowledge_extractor_v3.health as health_mod
    original = health_mod.load_absorption_prompt
    health_mod.load_absorption_prompt = lambda root: original(tmp_path)
    try:
        check = checker._check_prompt_registry()
    finally:
        health_mod.load_absorption_prompt = original

    assert check.status is HealthStatus.ERROR
    assert "Absorption prompt error" in check.message
