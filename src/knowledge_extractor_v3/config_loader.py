"""Production configuration loader for V3.

Loads config from config/config.local.yaml (untracked) with fallback to
config/config.example.yaml for defaults. Validates required sections,
resolves environment variable names, and expands paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


class ConfigLoaderError(RuntimeError):
    """Raised when config loading or validation fails."""


# ---------------------------------------------------------------------------
# Config dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RuntimeConfig:
    state_root: str = "~/.100x_v3"
    queue_db_path: str = "~/.100x_v3/queue.db"
    log_path: str = "~/100x-v3-daemon.log"


@dataclass(frozen=True)
class LiveConfig:
    enabled: bool = False
    require_runtime_guard: bool = True
    require_operator_confirmation: bool = False
    max_tasks_per_run: int = 0
    max_consecutive_failures: int = 5


@dataclass(frozen=True)
class LLMConfig:
    provider: str = "placeholder"
    api_key_env: str = "ZHIPU_API_KEY"
    scoring_model: str = ""
    extraction_model: str = ""
    telegram_brief_model: str = ""
    request_timeout_seconds: int = 60
    max_retries: int = 3
    min_delay_seconds: float = 2.0


@dataclass(frozen=True)
class OutputsConfig:
    obsidian_root: str = ""
    obsidian_subdir: str = "inbox"
    write_manifest: bool = True
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_admin_chat_id_env: str = "TELEGRAM_ADMIN_CHAT_ID"
    telegram_enabled: bool = True


@dataclass(frozen=True)
class PromptsConfig:
    registry: str = "prompts/registry.json"
    active_bundle: str = "primary_market_v1"
    parallel_test_bundles: list[str] = field(default_factory=list)
    scoring: str = "prompts/primary_market_scoring.md"
    extraction: str = "prompts/primary_market_extraction.md"
    telegram_brief: str = "prompts/telegram_brief.md"


@dataclass(frozen=True)
class SourceConfig:
    name: str = ""
    type: str = ""
    url: str = ""
    enabled: bool = True
    priority: int = 100
    tags: list[str] = field(default_factory=list)
    category: str = ""
    cron_interval: str = ""
    lookback_days: int = 7
    max_items: int = 10
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class SchedulerConfig:
    enabled: bool = False
    interval_seconds: int = 300


@dataclass(frozen=True)
class WorkerConfig:
    batch_size: int = 10
    poll_interval_seconds: int = 30


@dataclass(frozen=True)
class TelegramBotConfig:
    enabled: bool = False


@dataclass(frozen=True)
class AgentReachConfig:
    enabled: bool = True
    config_path: str = "~/.agent-reach/config.yaml"
    enabled_channels: list[str] = field(default_factory=list)
    fallback_to_jina: bool = True
    proxy: str = ""


@dataclass(frozen=True)
class V3Config:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    sources_files: list[str] = field(default_factory=list)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    telegram_bot: TelegramBotConfig = field(default_factory=TelegramBotConfig)
    agent_reach: AgentReachConfig = field(default_factory=AgentReachConfig)


# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, object]:
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        return _parse_yaml_subset(text)
    loaded = yaml.safe_load(text)
    if not isinstance(loaded, dict):
        raise ConfigLoaderError(f"YAML root must be a mapping: {path}")
    return loaded


def _load_yaml_sources(path: Path) -> list[object]:
    """Load a YAML file whose root may be either a mapping with sources or a list."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError as exc:
        return _parse_sources_yaml_subset(text)
    loaded = yaml.safe_load(text)
    if isinstance(loaded, dict):
        sources = loaded.get("sources", [])
        return sources if isinstance(sources, list) else []
    if isinstance(loaded, list):
        return loaded
    raise ConfigLoaderError(f"Sources YAML root must be mapping or list: {path}")


def _parse_sources_yaml_subset(text: str) -> list[object]:
    """Parse the simple config/sources.yaml shape without PyYAML."""
    sources: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    current_list_key: str | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#") or stripped == "sources:":
            continue

        if stripped.startswith("- "):
            item_text = stripped[2:].strip()
            if ":" in item_text:
                current = {}
                sources.append(current)
                key, _, value = item_text.partition(":")
                current[key.strip()] = _yaml_scalar(value.strip())
                current_list_key = None
            elif current is not None and current_list_key:
                current.setdefault(current_list_key, [])
                values = current[current_list_key]
                if isinstance(values, list):
                    values.append(_yaml_scalar(item_text))
            continue

        if current is not None and ":" in stripped:
            key, _, value = stripped.partition(":")
            key = key.strip()
            value = value.strip()
            if value:
                current[key] = _yaml_scalar(value)
                current_list_key = None
            else:
                current[key] = []
                current_list_key = key

    return sources


def _parse_yaml_subset(text: str) -> dict[str, object]:
    """Minimal YAML parser for simple configs when pyyaml is not installed."""
    data: dict[str, object] = {}
    current_section: str | None = None
    current_list_key: str | None = None
    section_indent = 0
    current_item: dict[str, object] | None = None

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip())

        # Top-level key
        if indent == 0 and ":" in stripped:
            key, _, raw_value = stripped.partition(":")
            key = key.strip()
            val = raw_value.strip()
            if not val:
                data[key] = {}
                current_section = key
                current_list_key = None
                section_indent = 0
            else:
                data[key] = _yaml_scalar(val)
                current_section = None
                current_list_key = None
            continue

        if current_section is not None and isinstance(data.get(current_section), dict):
            section_dict = data[current_section]
            assert isinstance(section_dict, dict)

            # List items (- value)
            if stripped.startswith("- "):
                if current_list_key is None:
                    # This is a list under the current section key
                    if indent <= 4 and isinstance(section_dict.get(current_list_key or ""), list):
                        pass
                    else:
                        # Detect list key from context
                        current_list_key = _find_list_parent(section_dict, indent, stripped)
                if current_list_key and isinstance(section_dict.get(current_list_key), list):
                    section_dict[current_list_key].append(
                        _yaml_scalar(stripped[2:].strip())
                    )
                continue

            # Key-value pairs
            if ":" in stripped:
                key, _, raw_value = stripped.partition(":")
                key = key.strip()
                val = raw_value.strip()
                if not val:
                    # Could be a nested section or a list
                    section_dict[key] = []
                    current_list_key = key
                else:
                    section_dict[key] = _yaml_scalar(val)
                continue

    return data


def _find_list_parent(
    section_dict: dict[str, object], indent: int, stripped: str
) -> str:
    """Find the key that should hold the current list item."""
    for k, v in section_dict.items():
        if isinstance(v, list):
            return k
    # If no list exists yet, try the last key
    return ""


def _yaml_scalar(value: str) -> object:
    if not value:
        return ""
    if value[0:1] == value[-1:] == '"':
        return value[1:-1]
    if value[0:1] == value[-1:] == "'":
        return value[1:-1]
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    if value.lower() == "null" or value == "~":
        return None
    try:
        return int(value)
    except ValueError:
        pass
    try:
        return float(value)
    except ValueError:
        pass
    return value


# ---------------------------------------------------------------------------
# Merge helper
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Merge override into base. Override values take precedence."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------


def _build_runtime(raw: dict[str, object]) -> RuntimeConfig:
    return RuntimeConfig(
        state_root=str(raw.get("state_root", "~/.100x_v3")),
        queue_db_path=str(raw.get("queue_db_path", "~/.100x_v3/queue.db")),
        log_path=str(raw.get("log_path", "~/100x-v3-daemon.log")),
    )


def _build_live(raw: dict[str, object]) -> LiveConfig:
    return LiveConfig(
        enabled=bool(raw.get("enabled", False)),
        require_runtime_guard=bool(raw.get("require_runtime_guard", True)),
        require_operator_confirmation=bool(raw.get("require_operator_confirmation", False)),
        max_tasks_per_run=int(raw.get("max_tasks_per_run", 0)),
        max_consecutive_failures=int(raw.get("max_consecutive_failures", 5)),
    )


def _build_llm(raw: dict[str, object]) -> LLMConfig:
    return LLMConfig(
        provider=str(raw.get("provider", "placeholder")),
        api_key_env=str(raw.get("api_key_env", "ZHIPU_API_KEY")),
        scoring_model=str(raw.get("scoring_model", "")),
        extraction_model=str(raw.get("extraction_model", "")),
        telegram_brief_model=str(raw.get("telegram_brief_model", "")),
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 60)),
        max_retries=int(raw.get("max_retries", 3)),
        min_delay_seconds=float(raw.get("min_delay_seconds", 2.0)),
    )


def _build_outputs(raw: dict[str, object]) -> OutputsConfig:
    return OutputsConfig(
        obsidian_root=str(raw.get("obsidian_root", "")),
        obsidian_subdir=str(raw.get("obsidian_subdir", "inbox")),
        write_manifest=bool(raw.get("write_manifest", True)),
        telegram_bot_token_env=str(raw.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")),
        telegram_admin_chat_id_env=str(raw.get("telegram_admin_chat_id_env", "TELEGRAM_ADMIN_CHAT_ID")),
        telegram_enabled=bool(raw.get("telegram_enabled", True)),
    )


def _build_prompts(raw: dict[str, object]) -> PromptsConfig:
    ptb = raw.get("parallel_test_bundles", [])
    return PromptsConfig(
        registry=str(raw.get("registry", "prompts/registry.json")),
        active_bundle=str(raw.get("active_bundle", "primary_market_v1")),
        parallel_test_bundles=[str(x) for x in ptb] if isinstance(ptb, list) else [],
        scoring=str(raw.get("scoring", "prompts/primary_market_scoring.md")),
        extraction=str(raw.get("extraction", "prompts/primary_market_extraction.md")),
        telegram_brief=str(raw.get("telegram_brief", "prompts/telegram_brief.md")),
    )


def _build_sources(raw: list[object]) -> list[SourceConfig]:
    if not isinstance(raw, list):
        return []
    configs = []
    for item in raw:
        if isinstance(item, dict):
            tags = item.get("tags", [])
            metadata = item.get("metadata", {})
            configs.append(SourceConfig(
                name=str(item.get("name", "")),
                type=str(item.get("type", "")),
                url=str(item.get("url", "")),
                enabled=bool(item.get("enabled", True)),
                priority=int(item.get("priority", 100)),
                tags=[str(x) for x in tags] if isinstance(tags, list) else [],
                category=str(item.get("category", "")),
                cron_interval=str(item.get("cron_interval", "")),
                lookback_days=int(item.get("lookback_days", 7)),
                max_items=int(item.get("max_items", 10)),
                metadata=metadata if isinstance(metadata, dict) else {},
            ))
    return configs


def _build_sources_files(raw: object) -> list[str]:
    if isinstance(raw, list):
        return [str(item) for item in raw]
    if isinstance(raw, str) and raw:
        return [raw]
    return []


def _build_scheduler(raw: dict[str, object]) -> SchedulerConfig:
    return SchedulerConfig(
        enabled=bool(raw.get("enabled", False)),
        interval_seconds=int(raw.get("interval_seconds", 300)),
    )


def _build_worker(raw: dict[str, object]) -> WorkerConfig:
    return WorkerConfig(
        batch_size=int(raw.get("batch_size", 10)),
        poll_interval_seconds=int(raw.get("poll_interval_seconds", 30)),
    )


def _build_telegram_bot(raw: dict[str, object]) -> TelegramBotConfig:
    return TelegramBotConfig(
        enabled=bool(raw.get("enabled", False)),
    )


def _build_agent_reach(raw: dict[str, object]) -> AgentReachConfig:
    enabled_channels = raw.get("enabled_channels", [])
    return AgentReachConfig(
        enabled=bool(raw.get("enabled", True)),
        config_path=str(raw.get("config_path", "~/.agent-reach/config.yaml")),
        enabled_channels=[str(x) for x in enabled_channels] if isinstance(enabled_channels, list) else [],
        fallback_to_jina=bool(raw.get("fallback_to_jina", True)),
        proxy=str(raw.get("proxy", "")),
    )


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------


class ConfigLoader:
    """Load and validate V3 configuration from YAML files.

    Resolution order:
      1. If explicit_path is given, load that file.
      2. Else if config/config.local.yaml exists, load it merged with example.
      3. Else load config/config.example.yaml as defaults.
    """

    def __init__(
        self,
        *,
        project_root: Path | None = None,
        explicit_path: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> None:
        self._project_root = Path(project_root or Path(__file__).resolve().parents[2])
        self._explicit_path = Path(explicit_path).expanduser() if explicit_path else None
        self._env = env or os.environ
        self._config_path_used: Path | None = None

    def load(self) -> V3Config:
        raw = self._load_raw()
        raw = self._with_external_sources(raw)
        self._validate_required(raw)
        return V3Config(
            runtime=_build_runtime(self._section(raw, "runtime")),
            live=_build_live(self._section(raw, "live")),
            llm=_build_llm(self._section(raw, "llm")),
            outputs=_build_outputs(self._section(raw, "outputs")),
            prompts=_build_prompts(self._section(raw, "prompts")),
            sources=_build_sources(raw.get("sources", [])),  # type: ignore[arg-type]
            sources_files=_build_sources_files(raw.get("sources_files", [])),
            scheduler=_build_scheduler(self._section(raw, "scheduler")),
            worker=_build_worker(self._section(raw, "worker")),
            telegram_bot=_build_telegram_bot(self._section(raw, "telegram_bot")),
            agent_reach=_build_agent_reach(self._section(raw, "agent_reach")),
        )

    def resolve_env(self, env_var_name: str) -> str:
        """Return value of env var, or empty string if missing."""
        return self._env.get(env_var_name, "")

    def expand_path(self, raw: str) -> Path:
        """Expand ~ and resolve relative paths against project root."""
        p = Path(raw).expanduser()
        if not p.is_absolute():
            p = self._project_root / p
        return p.resolve()

    @property
    def config_path_used(self) -> Path:
        if self._config_path_used is None:
            raise ConfigLoaderError("Config not loaded yet")
        return self._config_path_used

    @property
    def project_root(self) -> Path:
        return self._project_root

    @property
    def using_local_config(self) -> bool:
        """True if config was loaded from config.local.yaml (not example)."""
        if self._config_path_used is None:
            return False
        return self._config_path_used.name != "config.example.yaml"

    # -- internal -------------------------------------------------------

    def _load_raw(self) -> dict[str, object]:
        if self._explicit_path:
            if not self._explicit_path.exists():
                raise ConfigLoaderError(f"Config file not found: {self._explicit_path}")
            self._config_path_used = self._explicit_path
            return _load_yaml(self._explicit_path)

        local_path = self._project_root / "config" / "config.local.yaml"
        example_path = self._project_root / "config" / "config.example.yaml"

        if local_path.exists():
            self._config_path_used = local_path
            local_raw = _load_yaml(local_path)
            if example_path.exists():
                example_raw = _load_yaml(example_path)
                return _deep_merge(example_raw, local_raw)
            return local_raw

        if example_path.exists():
            self._config_path_used = example_path
            return _load_yaml(example_path)

        raise ConfigLoaderError(
            "No config file found. Expected config/config.local.yaml or "
            "config/config.example.yaml under project root."
        )

    def _with_external_sources(self, raw: dict[str, object]) -> dict[str, object]:
        """Merge sources from config/sources.yaml or configured sources_files.

        Local inline sources stay valid, while external files can fill the
        production source registry. Dedupe by URL first and name second so the
        current 39 inline RSS sources plus config/sources.yaml become the full
        91-source set instead of 130 duplicated entries.
        """
        merged = dict(raw)
        inline_sources = merged.get("sources", [])
        sources: list[object] = list(inline_sources) if isinstance(inline_sources, list) else []

        configured_files = _build_sources_files(merged.get("sources_files", []))
        default_sources = self._project_root / "config" / "sources.yaml"
        if not configured_files and default_sources.exists():
            configured_files = ["config/sources.yaml"]

        for raw_file in configured_files:
            path = self.expand_path(raw_file)
            if not path.exists():
                raise ConfigLoaderError(f"Sources file not found: {path}")
            sources.extend(_load_yaml_sources(path))

        merged["sources"] = self._dedupe_sources(sources)
        merged["sources_files"] = configured_files
        return merged

    @staticmethod
    def _dedupe_sources(sources: list[object]) -> list[object]:
        deduped: list[object] = []
        seen: set[str] = set()
        for item in sources:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            name = str(item.get("name", "")).strip()
            key = f"url:{url}" if url else f"name:{name}"
            if not key or key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    @staticmethod
    def _section(raw: dict[str, object], name: str) -> dict[str, object]:
        value = raw.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise ConfigLoaderError(f"Config section '{name}' must be a mapping")
        return value

    @staticmethod
    def _validate_required(raw: dict[str, object]) -> None:
        # Runtime is the only strictly required section
        if "runtime" not in raw:
            raise ConfigLoaderError("Config missing required 'runtime' section")
