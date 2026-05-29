"""Production configuration loader for V3.

Loads config from config/config.local.yaml (untracked), legacy
config/config.yaml, or config/config.example.yaml for defaults. Validates
required sections, resolves environment variable names, and expands paths.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional


class ConfigLoaderError(RuntimeError):
    """Raised when config loading or validation fails."""


DEFAULT_ZHIPU_API_BASE = "https://open.bigmodel.cn/api/coding/paas/v4"


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
    api_key: str = ""  # 直接配置 API key（优先于环境变量）
    api_base: str = DEFAULT_ZHIPU_API_BASE
    scoring_model: str = ""
    extraction_model: str = ""
    telegram_brief_model: str = ""
    request_timeout_seconds: int = 60
    max_retries: int = 3
    min_delay_seconds: float = 2.0
    temperature: float = 0.1
    fallback_providers: list[dict[str, object]] = field(default_factory=list)


@dataclass(frozen=True)
class OutputsConfig:
    obsidian_root: str = ""
    obsidian_subdir: str = "inbox"
    write_manifest: bool = True
    telegram_bot_token_env: str = "TELEGRAM_BOT_TOKEN"
    telegram_admin_chat_id_env: str = "TELEGRAM_ADMIN_CHAT_ID"
    telegram_bot_token: str = ""  # 直接配置 token（优先于环境变量，兼容 V2）
    telegram_admin_chat_id: str = ""  # 直接配置 chat_id（优先于环境变量，兼容 V2）
    telegram_enabled: bool = True


@dataclass(frozen=True)
class PromptsConfig:
    registry: str = "prompts/registry.json"
    active_bundle: str = "v2_stable_cn"
    parallel_test_bundles: list[str] = field(default_factory=list)
    scoring: str = "prompts/primary_market_scoring.md"
    extraction: str = "prompts/primary_market_extraction.md"
    telegram_brief: str = "prompts/telegram_brief.md"


@dataclass(frozen=True)
class ScoreGateConfig:
    enabled: bool = True
    reject_threshold: float = 0.3


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
class AgentReachWechatConfig:
    """WeChat-specific options for AgentReach fetcher."""
    headless_first: bool = True
    interactive_on_blocked: bool = True
    profile_dir: str = "~/.100x_v3/browser-profiles/wechat"
    verification_timeout_seconds: int = 300


@dataclass(frozen=True)
class AgentReachConfig:
    enabled: bool = True
    config_path: str = "~/.agent-reach/config.yaml"
    enabled_channels: list[str] = field(default_factory=list)
    fallback_to_jina: bool = True
    proxy: str = ""
    wechat: AgentReachWechatConfig = field(default_factory=AgentReachWechatConfig)


@dataclass(frozen=True)
class USAiMarketDailyReportConfig:
    enabled: bool = False
    timezone: str = "Asia/Shanghai"
    schedule_time: str = "08:03"
    system_dir: str = "~/Documents/Obsidian Vault/信息源/日报系统"
    stock_context_root: str = (
        "~/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览"
    )
    output_category: str = "日报"
    lookback_hours: int = 72
    non_trading_day_mode: str = "review"


@dataclass(frozen=True)
class DailyReportsConfig:
    us_ai_market: USAiMarketDailyReportConfig = field(
        default_factory=USAiMarketDailyReportConfig
    )


@dataclass(frozen=True)
class V3Config:
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)
    live: LiveConfig = field(default_factory=LiveConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    outputs: OutputsConfig = field(default_factory=OutputsConfig)
    prompts: PromptsConfig = field(default_factory=PromptsConfig)
    score_gate: ScoreGateConfig = field(default_factory=ScoreGateConfig)
    sources: list[SourceConfig] = field(default_factory=list)
    sources_files: list[str] = field(default_factory=list)
    scheduler: SchedulerConfig = field(default_factory=SchedulerConfig)
    worker: WorkerConfig = field(default_factory=WorkerConfig)
    telegram_bot: TelegramBotConfig = field(default_factory=TelegramBotConfig)
    agent_reach: AgentReachConfig = field(default_factory=AgentReachConfig)
    daily_reports: DailyReportsConfig = field(default_factory=DailyReportsConfig)


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


def _set_if_empty(target: dict[str, object], key: str, value: object) -> None:
    if value is None:
        return
    if key not in target or target.get(key) in ("", None):
        target[key] = value


def _normalize_legacy_v2(raw: dict[str, object]) -> dict[str, object]:
    """Accept the V2 config shape alongside the native V3 shape."""
    normalized = dict(raw)
    normalized.setdefault("runtime", {})

    llm_raw = normalized.get("llm", {})
    if isinstance(llm_raw, dict):
        llm = dict(llm_raw)
        _set_if_empty(llm, "api_base", DEFAULT_ZHIPU_API_BASE)
        router = llm.get("router", {})
        if isinstance(router, dict):
            route_map = {
                "quality_filter": "scoring_model",
                "deep_analysis": "extraction_model",
                "telegram_format": "telegram_brief_model",
            }
            for legacy_key, v3_key in route_map.items():
                route = router.get(legacy_key, {})
                if isinstance(route, dict):
                    _set_if_empty(llm, v3_key, route.get("model"))
        normalized["llm"] = llm

    output_raw = normalized.get("output", {})
    if isinstance(output_raw, dict):
        outputs_raw = normalized.get("outputs", {})
        outputs = dict(outputs_raw) if isinstance(outputs_raw, dict) else {}
        if output_raw.get("obsidian_root") is not None:
            outputs["obsidian_root"] = output_raw.get("obsidian_root")
        if output_raw.get("obsidian_folder") is not None:
            outputs["obsidian_subdir"] = output_raw.get("obsidian_folder")
        # V2 compatibility: telegram_token -> telegram_bot_token
        if output_raw.get("telegram_token") is not None:
            outputs["telegram_bot_token"] = output_raw.get("telegram_token")
        # V2 compatibility: telegram_chat_id -> telegram_admin_chat_id
        if output_raw.get("telegram_chat_id") is not None:
            outputs["telegram_admin_chat_id"] = output_raw.get("telegram_chat_id")
        normalized["outputs"] = outputs

    filter_raw = normalized.get("filter", {})
    if isinstance(filter_raw, dict):
        prompts_raw = normalized.get("prompts", {})
        prompts = dict(prompts_raw) if isinstance(prompts_raw, dict) else {}
        if filter_raw.get("scoring_prompt") is not None:
            prompts["scoring"] = filter_raw.get("scoring_prompt")
        normalized["prompts"] = prompts

    extraction_prompt = normalized.get("extraction_prompt")
    if extraction_prompt:
        prompts_raw = normalized.get("prompts", {})
        prompts = dict(prompts_raw) if isinstance(prompts_raw, dict) else {}
        prompts["extraction"] = extraction_prompt
        normalized["prompts"] = prompts

    return normalized


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


def _legacy_router_model(raw: dict[str, object], route_name: str) -> str:
    router_raw = raw.get("router", {})
    if not isinstance(router_raw, dict):
        return ""
    route = router_raw.get(route_name, {})
    if not isinstance(route, dict):
        return ""
    return str(route.get("model", ""))


def _non_placeholder(value: object) -> str:
    text = str(value or "")
    return "" if text.startswith("placeholder-") else text


def _build_llm(raw: dict[str, object]) -> LLMConfig:
    # Parse fallback_providers as list of dicts
    fallback_raw = raw.get("fallback_providers", [])
    fallback_providers: list[dict[str, object]] = []
    if isinstance(fallback_raw, list):
        for item in fallback_raw:
            if isinstance(item, dict):
                fallback_providers.append({
                    "provider": str(item.get("provider", "openai")),
                    "api_key_env": str(item.get("api_key_env", "OPENAI_API_KEY")),
                    "api_key": str(item.get("api_key", "")),
                    "api_base": str(item.get("api_base", "")),
                    "scoring_model": str(item.get("scoring_model", "")),
                    "extraction_model": str(item.get("extraction_model", "")),
                    "telegram_model": str(item.get("telegram_model", item.get("telegram_brief_model", ""))),
                    "temperature": float(item.get("temperature", raw.get("temperature", 0.1))),
                })

    provider = str(raw.get("provider", "placeholder"))
    scoring_model = (
        _non_placeholder(raw.get("scoring_model"))
        or _legacy_router_model(raw, "quality_filter")
    )
    extraction_model = (
        _non_placeholder(raw.get("extraction_model"))
        or _legacy_router_model(raw, "deep_analysis")
    )
    telegram_brief_model = (
        _non_placeholder(raw.get("telegram_brief_model"))
        or _legacy_router_model(raw, "telegram_format")
    )

    return LLMConfig(
        provider=provider,
        api_key_env=str(raw.get("api_key_env", "ZHIPU_API_KEY")),
        api_key=str(raw.get("api_key", "")),
        api_base=str(raw.get("api_base", DEFAULT_ZHIPU_API_BASE if provider == "zhipu" else "")),
        scoring_model=scoring_model,
        extraction_model=extraction_model,
        telegram_brief_model=telegram_brief_model,
        request_timeout_seconds=int(raw.get("request_timeout_seconds", 60)),
        max_retries=int(raw.get("max_retries", 3)),
        min_delay_seconds=float(raw.get("min_delay_seconds", 2.0)),
        temperature=float(raw.get("temperature", 0.1)),
        fallback_providers=fallback_providers,
    )


def _build_outputs(raw: dict[str, object]) -> OutputsConfig:
    return OutputsConfig(
        obsidian_root=str(raw.get("obsidian_root", "")),
        obsidian_subdir=str(raw.get("obsidian_subdir", "inbox")),
        write_manifest=bool(raw.get("write_manifest", True)),
        telegram_bot_token_env=str(raw.get("telegram_bot_token_env", "TELEGRAM_BOT_TOKEN")),
        telegram_admin_chat_id_env=str(raw.get("telegram_admin_chat_id_env", "TELEGRAM_ADMIN_CHAT_ID")),
        telegram_bot_token=str(raw.get("telegram_bot_token", "")),
        telegram_admin_chat_id=str(raw.get("telegram_admin_chat_id", "")),
        telegram_enabled=bool(raw.get("telegram_enabled", True)),
    )


def _build_prompts(raw: dict[str, object]) -> PromptsConfig:
    ptb = raw.get("parallel_test_bundles", [])
    return PromptsConfig(
        registry=str(raw.get("registry", "prompts/registry.json")),
        active_bundle=str(raw.get("active_bundle", "v2_stable_cn")),
        parallel_test_bundles=[str(x) for x in ptb] if isinstance(ptb, list) else [],
        scoring=str(raw.get("scoring", "prompts/primary_market_scoring.md")),
        extraction=str(raw.get("extraction", "prompts/primary_market_extraction.md")),
        telegram_brief=str(raw.get("telegram_brief", "prompts/telegram_brief.md")),
    )


def _normalize_reject_threshold(value: object, *, default: float = 0.3) -> float:
    try:
        threshold = float(value)
    except (TypeError, ValueError):
        return default
    if threshold > 1:
        threshold = threshold / 10
    return max(0.0, min(1.0, threshold))


def _build_score_gate(raw: dict[str, object], legacy_score_threshold: object = None) -> ScoreGateConfig:
    threshold_raw = raw.get(
        "reject_threshold",
        raw.get("threshold", legacy_score_threshold if legacy_score_threshold is not None else 0.3),
    )
    return ScoreGateConfig(
        enabled=bool(raw.get("enabled", True)),
        reject_threshold=_normalize_reject_threshold(threshold_raw),
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
    wechat_raw = raw.get("wechat", {})
    if isinstance(wechat_raw, dict):
        wechat_cfg = AgentReachWechatConfig(
            headless_first=bool(wechat_raw.get("headless_first", True)),
            interactive_on_blocked=bool(wechat_raw.get("interactive_on_blocked", True)),
            profile_dir=str(wechat_raw.get("profile_dir", "~/.100x_v3/browser-profiles/wechat")),
            verification_timeout_seconds=int(wechat_raw.get("verification_timeout_seconds", 300)),
        )
    else:
        wechat_cfg = AgentReachWechatConfig()

    return AgentReachConfig(
        enabled=bool(raw.get("enabled", True)),
        config_path=str(raw.get("config_path", "~/.agent-reach/config.yaml")),
        enabled_channels=[str(x) for x in enabled_channels] if isinstance(enabled_channels, list) else [],
        fallback_to_jina=bool(raw.get("fallback_to_jina", True)),
        proxy=str(raw.get("proxy", "")),
        wechat=wechat_cfg,
    )


def _build_us_ai_market_daily_report(
    raw: dict[str, object],
) -> USAiMarketDailyReportConfig:
    return USAiMarketDailyReportConfig(
        enabled=bool(raw.get("enabled", False)),
        timezone=str(raw.get("timezone", "Asia/Shanghai")),
        schedule_time=str(raw.get("schedule_time", "08:03")),
        system_dir=str(
            raw.get("system_dir", "~/Documents/Obsidian Vault/信息源/日报系统")
        ),
        stock_context_root=str(
            raw.get(
                "stock_context_root",
                "~/Documents/Obsidian Vault/兴趣领域/股票投资/AI周期探索/0_总览",
            )
        ),
        output_category=str(raw.get("output_category", "日报")),
        lookback_hours=int(raw.get("lookback_hours", 72)),
        non_trading_day_mode=str(raw.get("non_trading_day_mode", "review")),
    )


def _build_daily_reports(raw: dict[str, object]) -> DailyReportsConfig:
    us_ai_market_raw = raw.get("us_ai_market", {})
    if not isinstance(us_ai_market_raw, dict):
        us_ai_market_raw = {}
    return DailyReportsConfig(
        us_ai_market=_build_us_ai_market_daily_report(us_ai_market_raw)
    )


# ---------------------------------------------------------------------------
# ConfigLoader
# ---------------------------------------------------------------------------


class ConfigLoader:
    """Load and validate V3 configuration from YAML files.

    Resolution order:
      1. If explicit_path is given, load that file.
      2. Else if config/config.local.yaml exists, load it merged with example.
      3. Else if config/config.yaml exists, load it as a V2-compatible config.
      4. Else load config/config.example.yaml as defaults.
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
        raw = _normalize_legacy_v2(raw)
        raw = self._with_external_sources(raw)
        self._validate_required(raw)
        return V3Config(
            runtime=_build_runtime(self._section(raw, "runtime")),
            live=_build_live(self._section(raw, "live")),
            llm=_build_llm(self._section(raw, "llm")),
            outputs=_build_outputs(self._section(raw, "outputs")),
            prompts=_build_prompts(self._section(raw, "prompts")),
            score_gate=_build_score_gate(self._section(raw, "score_gate"), raw.get("score_threshold")),
            sources=_build_sources(raw.get("sources", [])),  # type: ignore[arg-type]
            sources_files=_build_sources_files(raw.get("sources_files", [])),
            scheduler=_build_scheduler(self._section(raw, "scheduler")),
            worker=_build_worker(self._section(raw, "worker")),
            telegram_bot=_build_telegram_bot(self._section(raw, "telegram_bot")),
            agent_reach=_build_agent_reach(self._section(raw, "agent_reach")),
            daily_reports=_build_daily_reports(self._section(raw, "daily_reports")),
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
        legacy_path = self._project_root / "config" / "config.yaml"
        example_path = self._project_root / "config" / "config.example.yaml"

        if local_path.exists():
            self._config_path_used = local_path
            local_raw = _load_yaml(local_path)
            if example_path.exists():
                example_raw = _load_yaml(example_path)
                return _deep_merge(example_raw, local_raw)
            return local_raw

        if legacy_path.exists():
            self._config_path_used = legacy_path
            return _load_yaml(legacy_path)

        if example_path.exists():
            self._config_path_used = example_path
            return _load_yaml(example_path)

        raise ConfigLoaderError(
            "No config file found. Expected config/config.local.yaml, "
            "config/config.yaml, or config/config.example.yaml under project root."
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
