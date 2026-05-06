"""Runtime Guard contract for V3.

The guard fails loudly when a V3 process points at V2 state, a shadow HOME, or
an incompatible queue schema. It is designed to run before any daemon,
scheduler, bot, or queue worker is introduced.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping, Optional

from .queue_store import QUEUE_REQUIRED_COLUMNS


class RuntimeGuardError(RuntimeError):
    """Raised when V3 runtime isolation checks fail."""


@dataclass(frozen=True)
class RuntimePaths:
    project_root: Path
    state_root: Path
    queue_db_path: Path
    log_path: Path
    config_path: Path
    home: Path

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> "RuntimePaths":
        effective_env = env or os.environ
        root = Path(project_root or Path(__file__).resolve().parents[2]).expanduser()
        home = Path(effective_env.get("HOME", str(Path.home()))).expanduser()
        state_root = Path(effective_env.get("STATE_ROOT", str(home / ".100x_v3"))).expanduser()
        queue_db_path = Path(
            effective_env.get("QUEUE_DB_PATH", str(state_root / "queue.db"))
        ).expanduser()
        log_path = Path(effective_env.get("LOG_PATH", str(home / "100x-v3-daemon.log"))).expanduser()
        config_path = Path(
            effective_env.get("CONFIG_PATH", str(root / "config" / "config.local.yaml"))
        ).expanduser()
        return cls(
            project_root=root,
            state_root=state_root,
            queue_db_path=queue_db_path,
            log_path=log_path,
            config_path=config_path,
            home=home,
        )


@dataclass(frozen=True)
class RuntimeFingerprint:
    project_root: str
    python_executable: str
    python_version: str
    platform: str
    config_path: str
    queue_db_path: str
    state_root: str
    log_path: str
    prompt_hashes: dict[str, str]
    source_hash: str
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)


class RuntimeGuard:
    """Validate V3 runtime isolation before any live role starts."""

    SHADOW_HOME_MARKERS = ("codepilot-shadow",)

    def __init__(self, paths: RuntimePaths) -> None:
        self.paths = paths

    @classmethod
    def from_env(
        cls,
        *,
        project_root: Optional[Path] = None,
        env: Optional[Mapping[str, str]] = None,
    ) -> "RuntimeGuard":
        return cls(RuntimePaths.from_env(project_root=project_root, env=env))

    def validate(self, *, write_fingerprint: bool = False) -> RuntimeFingerprint:
        self._validate_paths()
        self._validate_existing_queue_schema()
        fingerprint = self.build_fingerprint()
        if write_fingerprint:
            self.write_fingerprint(fingerprint)
        return fingerprint

    def build_fingerprint(self) -> RuntimeFingerprint:
        return RuntimeFingerprint(
            project_root=str(self.paths.project_root),
            python_executable=sys.executable,
            python_version=sys.version.split()[0],
            platform=platform.platform(),
            config_path=str(self.paths.config_path),
            queue_db_path=str(self.paths.queue_db_path),
            state_root=str(self.paths.state_root),
            log_path=str(self.paths.log_path),
            prompt_hashes=self._hash_prompt_files(),
            source_hash=self._hash_source_files(),
            created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        )

    def write_fingerprint(self, fingerprint: RuntimeFingerprint) -> Path:
        self.paths.state_root.mkdir(parents=True, exist_ok=True)
        destination = self.paths.state_root / "runtime_fingerprint.json"
        destination.write_text(fingerprint.to_json() + "\n", encoding="utf-8")
        return destination

    def _validate_paths(self) -> None:
        path_map = {
            "project_root": self.paths.project_root,
            "state_root": self.paths.state_root,
            "queue_db_path": self.paths.queue_db_path,
            "log_path": self.paths.log_path,
            "config_path": self.paths.config_path,
            "home": self.paths.home,
        }

        for label, path in path_map.items():
            if self._contains_v2_marker(path):
                raise RuntimeGuardError(f"{label} points at V2 state or source: {path}")

        if self._is_shadow_home(self.paths.home):
            raise RuntimeGuardError(f"Refusing to run under shadow HOME: {self.paths.home}")

        if not self._is_relative_to(self.paths.queue_db_path, self.paths.state_root):
            raise RuntimeGuardError(
                "QUEUE_DB_PATH must live under STATE_ROOT in V3: "
                f"{self.paths.queue_db_path} not under {self.paths.state_root}"
            )

        if self.paths.project_root.name != "v3":
            raise RuntimeGuardError(f"Project root must be the V3 repository root: {self.paths.project_root}")

    def _validate_existing_queue_schema(self) -> None:
        if not self.paths.queue_db_path.exists():
            return

        with sqlite3.connect(self.paths.queue_db_path) as conn:
            rows = conn.execute("PRAGMA table_info(queue)").fetchall()
        columns = {row[1] for row in rows}
        missing = QUEUE_REQUIRED_COLUMNS - columns
        if missing:
            missing_list = ", ".join(sorted(missing))
            raise RuntimeGuardError(f"Existing queue database is not V3-compatible: {missing_list}")

    def _hash_prompt_files(self) -> dict[str, str]:
        prompt_dir = self.paths.project_root / "prompts"
        hashes: dict[str, str] = {}
        if not prompt_dir.exists():
            return hashes
        for path in sorted(prompt_dir.glob("*.md")):
            hashes[path.name] = self._sha256(path)
        return hashes

    def _hash_source_files(self) -> str:
        package_dir = self.paths.project_root / "src" / "knowledge_extractor_v3"
        digest = hashlib.sha256()
        if not package_dir.exists():
            return digest.hexdigest()
        for path in sorted(package_dir.glob("*.py")):
            digest.update(path.name.encode("utf-8"))
            digest.update(self._sha256(path).encode("ascii"))
        return digest.hexdigest()

    @staticmethod
    def _sha256(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @staticmethod
    def _contains_v2_marker(path: Path) -> bool:
        text = str(path.expanduser())
        return ".100x_v2" in text or "/knowledge-extractor/v2" in text

    @classmethod
    def _is_shadow_home(cls, path: Path) -> bool:
        return any(marker in part for part in path.parts for marker in cls.SHADOW_HOME_MARKERS)

    @staticmethod
    def _is_relative_to(child: Path, parent: Path) -> bool:
        try:
            child.expanduser().resolve(strict=False).relative_to(parent.expanduser().resolve(strict=False))
            return True
        except ValueError:
            return False
