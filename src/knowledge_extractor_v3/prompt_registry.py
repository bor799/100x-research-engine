"""Prompt version registry for V3.

The registry lets scoring and extraction prompts change independently from
code. Multiple bundles can be resolved for parallel offline evaluation before
one bundle becomes active for live processing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


class PromptRegistryError(RuntimeError):
    """Raised when prompt registry configuration is invalid."""


@dataclass(frozen=True)
class PromptRoleSpec:
    role: str
    path: Path


@dataclass(frozen=True)
class PromptBundle:
    name: str
    label: str
    description: str
    roles: dict[str, Path]

    def prompt_path(self, role: str) -> Path:
        try:
            return self.roles[role]
        except KeyError as exc:
            raise PromptRegistryError(f"Bundle {self.name!r} does not define role {role!r}") from exc


class PromptRegistry:
    """Load versioned prompt bundles from a JSON registry."""

    def __init__(self, registry_path: Path) -> None:
        self.registry_path = Path(registry_path)
        self.root = self.registry_path.resolve().parents[1]
        self._data = self._load()

    @classmethod
    def default(cls, project_root: Path) -> "PromptRegistry":
        return cls(Path(project_root) / "prompts" / "registry.json")

    @property
    def active_bundle_name(self) -> str:
        name = self._data.get("active_bundle", "")
        if not name:
            raise PromptRegistryError("Prompt registry must define active_bundle")
        return name

    @property
    def parallel_test_bundle_names(self) -> list[str]:
        values = self._data.get("parallel_test_bundles", [])
        if not isinstance(values, list):
            raise PromptRegistryError("parallel_test_bundles must be a list")
        return [str(value) for value in values]

    def active_bundle(self) -> PromptBundle:
        return self.bundle(self.active_bundle_name)

    def bundle(self, name: str) -> PromptBundle:
        bundles = self._data.get("bundles", {})
        if name not in bundles:
            raise PromptRegistryError(f"Prompt bundle not found: {name}")

        raw = bundles[name]
        raw_roles = raw.get("roles", {})
        roles = {
            role: self._resolve_prompt_path(path)
            for role, path in raw_roles.items()
        }
        return PromptBundle(
            name=name,
            label=raw.get("label", name),
            description=raw.get("description", ""),
            roles=roles,
        )

    def bundles_for_parallel_test(self) -> list[PromptBundle]:
        return [self.bundle(name) for name in self.parallel_test_bundle_names]

    def load_prompt(self, bundle_name: str, role: str) -> str:
        path = self.bundle(bundle_name).prompt_path(role)
        return path.read_text(encoding="utf-8")

    def validate(self, *, required_roles: Iterable[str] = ("scoring", "extraction")) -> None:
        seen = {self.active_bundle_name, *self.parallel_test_bundle_names}
        for bundle_name in seen:
            bundle = self.bundle(bundle_name)
            for role in required_roles:
                path = bundle.prompt_path(role)
                if not path.exists():
                    raise PromptRegistryError(f"Missing prompt file for {bundle_name}.{role}: {path}")
                if not path.read_text(encoding="utf-8").strip():
                    raise PromptRegistryError(f"Empty prompt file for {bundle_name}.{role}: {path}")

    def _load(self) -> dict:
        if not self.registry_path.exists():
            raise PromptRegistryError(f"Prompt registry does not exist: {self.registry_path}")
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise PromptRegistryError(f"Prompt registry is invalid JSON: {self.registry_path}") from exc

    def _resolve_prompt_path(self, raw_path: str) -> Path:
        path = Path(raw_path)
        if path.is_absolute():
            return path
        return self.root / path
