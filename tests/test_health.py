from knowledge_extractor_v3.health import HealthChecker, HealthStatus


def test_worst_status_accepts_generators():
    status = HealthChecker._worst_status(
        item for item in [HealthStatus.HEALTHY, HealthStatus.ERROR]
    )

    assert status is HealthStatus.ERROR
