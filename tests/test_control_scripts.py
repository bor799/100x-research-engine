from __future__ import annotations

import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_control_script_syntax():
    result = subprocess.run(
        ["bash", "-n", "scripts/control.sh"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_control_print_plist_contains_launchagent_contract():
    result = subprocess.run(
        ["scripts/control.sh", "print-plist", "worker-loop"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "com.100x.v3.worker-loop" in result.stdout
    assert "<key>RunAtLoad</key><true/>" in result.stdout
    assert "<key>KeepAlive</key><true/>" in result.stdout
    assert "role-run" in result.stdout


def test_control_status_reports_sources():
    result = subprocess.run(
        ["scripts/control.sh", "status"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "Sources loaded: 95" in result.stdout
