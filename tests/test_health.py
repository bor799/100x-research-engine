import json
from datetime import UTC, datetime
from pathlib import Path

from knowledge_extractor_v3.config_loader import RuntimeConfig, V3Config
from knowledge_extractor_v3.health import HealthChecker, HealthStatus
from knowledge_extractor_v3.prompt_registry import PromptRegistry


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


def test_prompt_registry_health_reports_active_bundle_and_hash():
    registry = PromptRegistry.default(PROJECT_ROOT)
    checker = HealthChecker(V3Config(), prompt_registry=registry)

    check = checker._check_prompt_registry()

    assert check.status is HealthStatus.HEALTHY
    assert check.detail["active_bundle"] == "v2_stable_cn"
    assert check.detail["prompt_hash"]
