"""V3-native multi-channel fetcher.

This replaces the previous sys.path import of V2 Agent Reach. V2 remains a
reference system only; this module provides the V3 adapter surface and typed
errors expected by the worker/router.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..models import FetchedContent, TypedError, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction
from .http_client import HttpClient, create_http_client
from .rss_channel import RSSChannelAdapter
from .social import RedditChannelAdapter, V2EXChannelAdapter, HackerNewsChannelAdapter


@dataclass(frozen=True)
class WechatConfig:
    """WeChat fetcher configuration passed from V3Config."""
    headless_first: bool = True
    interactive_on_blocked: bool = True
    profile_dir: str = "~/.100x_v3/browser-profiles/wechat"
    verification_timeout_seconds: int = 300


class BaseChannelAdapter(ABC):
    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def can_handle(self, url: str) -> bool:
        ...

    @abstractmethod
    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def check(self, config: dict[str, Any]) -> str:
        ...

    @staticmethod
    def _domain(url: str) -> str:
        return urlparse(url).netloc.lower().removeprefix("www.")


class WebChannelAdapter(BaseChannelAdapter):
    @property
    def name(self) -> str:
        return "web"

    def can_handle(self, url: str) -> bool:
        return url.startswith(("http://", "https://"))

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(
                timeout=int(config.get("timeout", 30)),
                proxy=_proxy_from_config(config) or None,
            )
        # Strategy 1: Jina Reader (produces clean markdown, best for LLM)
        response = client.get_via_jina(url)
        if not isinstance(response, TypedError) and response.is_success:
            body = _body_from_jina(response.content)
            if len(body.strip()) > 100:
                return {
                    "title": _title_from_jina(response.content) or url[:100],
                    "content": body,
                    "source": "agent-reach-web",
                    "metadata": {"via_jina": True},
                }
        # Strategy 2: Direct HTTP GET (fallback — raw HTML, lower quality)
        response = client.get(url)
        if not isinstance(response, TypedError) and response.is_success:
            content = response.content
            if len(content.strip()) > 200:
                return {
                    "title": _title_from_html(content) or url[:100],
                    "content": _body_from_html(content),
                    "source": "agent-reach-web",
                    "metadata": {"via": "direct"},
                }
        return None

    def check(self, config: dict[str, Any]) -> str:
        client = config.get("http_client")
        if not isinstance(client, HttpClient):
            client = create_http_client(
                timeout=5,
                max_retries=1,
                proxy=_proxy_from_config(config) or None,
            )
        result = client.get_via_jina("https://example.com")
        if not isinstance(result, TypedError) and result.is_success:
            return "ok"
        direct = client.get("https://example.com")
        return "ok" if not isinstance(direct, TypedError) and direct.is_success else "error"


class TwitterChannelAdapter(BaseChannelAdapter):
    domains = {"x.com", "twitter.com", "mobile.x.com", "mobile.twitter.com"}

    @property
    def name(self) -> str:
        return "twitter"

    def can_handle(self, url: str) -> bool:
        return self._domain(url) in self.domains

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        xreach = config.get("xreach_path") or shutil.which("xreach")
        if not xreach:
            return None
        cmd = [str(xreach), "tweet", url, "--json"]
        auth_token = config.get("twitter_auth_token") or config.get("twitter", {}).get("auth_token", "")
        ct0 = config.get("twitter_ct0") or config.get("twitter", {}).get("ct0", "")
        if auth_token and ct0:
            cmd.extend(["--auth-token", str(auth_token), "--ct0", str(ct0)])
        proxy = (
            config.get("proxy")
            or config.get("twitter_proxy")
            or config.get("twitter", {}).get("proxy", "")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
        )
        if proxy:
            cmd.extend(["--proxy", str(proxy)])
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        text = str(data.get("text", "")).strip()
        if not text:
            return None
        user = data.get("user", {})
        username = user.get("screenName", "") if isinstance(user, dict) else ""
        return {
            "title": f"@{username}" if username else text[:100],
            "content": text,
            "source": "agent-reach-twitter",
            "author": username,
            "published_at": str(data.get("createdAt", "")),
            "metadata": {"raw": data},
        }

    def check(self, config: dict[str, Any]) -> str:
        xreach = config.get("xreach_path") or shutil.which("xreach")
        if not xreach:
            return "not_installed"

        auth_token = config.get("twitter_auth_token") or config.get("twitter", {}).get("auth_token", "")
        ct0 = config.get("twitter_ct0") or config.get("twitter", {}).get("ct0", "")
        if auth_token and ct0:
            return "ok"

        proxy = (
            config.get("proxy")
            or config.get("twitter_proxy")
            or config.get("twitter", {}).get("proxy", "")
            or os.environ.get("HTTPS_PROXY")
            or os.environ.get("HTTP_PROXY")
            or os.environ.get("ALL_PROXY")
        )
        cmd = [str(xreach), "auth", "check"]
        if proxy:
            cmd.extend(["--proxy", str(proxy)])
        try:
            completed = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return "missing_config"
        return "ok" if completed.returncode == 0 else "missing_config"


class YouTubeChannelAdapter(BaseChannelAdapter):
    domains = {"youtube.com", "youtu.be", "m.youtube.com", "www.youtube.com"}

    @property
    def name(self) -> str:
        return "youtube"

    def can_handle(self, url: str) -> bool:
        return self._domain(url) in self.domains

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        yt_dlp = config.get("yt_dlp_path") or shutil.which("yt-dlp")
        if not yt_dlp:
            return None
        cmd = [str(yt_dlp), "--dump-json", "--skip-download", "--no-warnings"]
        youtube_config = config.get("youtube", {})
        if not isinstance(youtube_config, dict):
            youtube_config = {}
        cookies_from_browser = (
            config.get("yt_dlp_cookies_from_browser")
            or youtube_config.get("cookies_from_browser")
        )
        cookies_path = config.get("yt_dlp_cookies") or youtube_config.get("cookies")
        proxy = config.get("proxy") or youtube_config.get("proxy")
        if cookies_from_browser:
            cmd.extend(["--cookies-from-browser", str(cookies_from_browser)])
        if cookies_path:
            cmd.extend(["--cookies", os.path.expanduser(str(cookies_path))])
        if proxy:
            cmd.extend(["--proxy", str(proxy)])
        cmd.append(url)
        try:
            completed = subprocess.run(cmd, capture_output=True, text=True, timeout=90, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
        if completed.returncode != 0:
            return None
        try:
            data = json.loads(completed.stdout)
        except json.JSONDecodeError:
            return None
        description = str(data.get("description", "")).strip()
        title = str(data.get("title", "")).strip()
        if not description and not title:
            return None
        return {
            "title": title or url[:100],
            "content": "\n\n".join(part for part in [title, description] if part),
            "source": "agent-reach-youtube",
            "author": str(data.get("uploader", "")),
            "published_at": str(data.get("upload_date", "")),
            "metadata": {"raw": {"id": data.get("id"), "duration": data.get("duration")}},
        }

    def check(self, config: dict[str, Any]) -> str:
        return "ok" if config.get("yt_dlp_path") or shutil.which("yt-dlp") else "not_installed"


class WechatChannelAdapter(BaseChannelAdapter):
    default_tool_path = Path.home() / ".agent-reach" / "tools" / "wechat-article-for-ai"
    default_profile_dir = Path.home() / ".100x_v3" / "browser-profiles" / "wechat"

    @property
    def name(self) -> str:
        return "wechat"

    def can_handle(self, url: str) -> bool:
        return self._domain(url) == "mp.weixin.qq.com"

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        tool_path = self._tool_path(config)
        wechat_cfg = config.get("wechat", {})
        if not isinstance(wechat_cfg, dict):
            wechat_cfg = {}

        # Strategy 1: Try headless mode first if configured
        if wechat_cfg.get("headless_first", True):
            if tool_path.exists():
                result = self._fetch_in_process(url, config, tool_path, headless=True)
                if result and not self._is_verification_result(result):
                    return result

                result = self._fetch_subprocess(url, config, tool_path, headless=True)
                if result and not self._is_verification_result(result):
                    return result

        # Strategy 2: Try visible browser with profile if interactive_on_blocked
        if wechat_cfg.get("interactive_on_blocked", True):
            if tool_path.exists():
                profile_dir = self._profile_dir(config)
                profile_dir.mkdir(parents=True, exist_ok=True)

                result = self._fetch_in_process(url, config, tool_path, headless=False, profile_dir=profile_dir)
                if result and not self._is_verification_result(result):
                    return result

                result = self._fetch_subprocess(url, config, tool_path, headless=False, profile_dir=profile_dir)
                if result and not self._is_verification_result(result):
                    return result

        # Strategy 3: Fallback to Jina
        fallback = WebChannelAdapter().fetch(url, config)
        if not fallback:
            return None
        fallback["source"] = "agent-reach-wechat"
        metadata = fallback.setdefault("metadata", {})
        if isinstance(metadata, dict):
            metadata["wechat_fallback"] = "jina"
        return fallback

    def check(self, config: dict[str, Any]) -> str:
        return "ok" if self._tool_path(config).exists() else "missing_config"

    def _tool_path(self, config: dict[str, Any]) -> Path:
        wechat_config = config.get("wechat", {})
        if not isinstance(wechat_config, dict):
            wechat_config = {}
        raw_path = (
            config.get("wechat_tool_path")
            or wechat_config.get("tool_path")
            or self.default_tool_path
        )
        return Path(os.path.expanduser(str(raw_path)))

    def _profile_dir(self, config: dict[str, Any]) -> Path:
        wechat_config = config.get("wechat", {})
        if not isinstance(wechat_config, dict):
            wechat_config = {}
        raw_path = wechat_config.get("profile_dir") or config.get("wechat_profile_dir")
        if raw_path:
            return Path(os.path.expanduser(str(raw_path)))
        return self.default_profile_dir

    @staticmethod
    def _is_verification_result(result: dict[str, Any] | None) -> bool:
        if not result:
            return False
        content = str(result.get("content", ""))
        return _is_wechat_verification_page(content)

    def _fetch_in_process(
        self,
        url: str,
        config: dict[str, Any],
        tool_path: Path,
        *,
        headless: bool = True,
        profile_dir: Path | None = None,
    ) -> dict[str, Any] | None:
        if not (tool_path / "wechat_to_md").exists():
            return None

        tool_str = str(tool_path)
        added = tool_str not in sys.path
        if added:
            sys.path.insert(0, tool_str)
        previous_env = _apply_proxy_env(config)
        try:
            from bs4 import BeautifulSoup  # type: ignore[import-untyped]
            from wechat_to_md.converter import build_markdown, convert_html_to_markdown  # type: ignore[import-not-found]
            from wechat_to_md.parser import extract_metadata, process_content  # type: ignore[import-not-found]
            from wechat_to_md.scraper import fetch_page_html  # type: ignore[import-not-found]

            # Pass headless to the scraper
            html = asyncio.run(fetch_page_html(
                url,
                headless=headless,
            ))
            soup = BeautifulSoup(html, "html.parser")
            meta = extract_metadata(soup, html, url=url)
            if not getattr(meta, "title", ""):
                return None
            parsed = process_content(soup)
            if not getattr(parsed, "content_html", "").strip():
                return None
            markdown_body = convert_html_to_markdown(parsed.content_html, parsed.code_blocks)
            final_markdown = build_markdown(
                meta,
                markdown_body,
                parsed.media_references,
                use_frontmatter=True,
            )
            if _is_wechat_verification_page(final_markdown):
                return None
            return {
                "title": getattr(meta, "title", "") or url[:100],
                "content": final_markdown,
                "source": "agent-reach-wechat",
                "author": getattr(meta, "author", "") or "Wechat",
                "published_at": getattr(meta, "date", "") or "",
                "metadata": {
                    "agent_reach_tool": "wechat-article-for-ai",
                    "agent_reach_tool_path": str(tool_path),
                    "agent_reach_tool_mode": "in_process",
                    "headless": headless,
                    "interactive_mode": not headless,
                },
            }
        except Exception:
            return None
        finally:
            _restore_proxy_env(previous_env)
            if added and tool_str in sys.path:
                sys.path.remove(tool_str)

    def _fetch_subprocess(
        self,
        url: str,
        config: dict[str, Any],
        tool_path: Path,
        *,
        headless: bool = True,
        profile_dir: Path | None = None,
    ) -> dict[str, Any] | None:
        main_py = tool_path / "main.py"
        if not main_py.exists():
            return None

        with tempfile.TemporaryDirectory(prefix="100x-wechat-") as temp_dir:
            output_dir = Path(temp_dir)
            cmd = [
                sys.executable,
                "main.py",
                url,
                "--no-images",
                "--force",
                "-o",
                str(output_dir),
            ]
            # Add headless and profile options if supported
            if not headless:
                cmd.append("--visible")
            if profile_dir:
                cmd.extend(["--profile", str(profile_dir)])

            wechat_cfg = config.get("wechat", {})
            timeout = wechat_cfg.get("verification_timeout_seconds", 120)

            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=False,
                    cwd=str(tool_path),
                    env=_proxy_env(config),
                )
            except (OSError, subprocess.TimeoutExpired):
                return None
            if completed.returncode != 0:
                return None

            markdown_files = sorted(
                output_dir.glob("**/*.md"),
                key=lambda path: path.stat().st_mtime,
                reverse=True,
            )
            if not markdown_files:
                return None
            markdown_path = markdown_files[0]
            content = markdown_path.read_text(encoding="utf-8")
            if not content.strip() or _is_wechat_verification_page(content):
                return None
            return {
                "title": _title_from_wechat_markdown(content) or markdown_path.stem,
                "content": content,
                "source": "agent-reach-wechat",
                "author": "Wechat",
                "metadata": {
                    "agent_reach_tool": "wechat-article-for-ai",
                    "agent_reach_tool_path": str(tool_path),
                    "agent_reach_tool_mode": "subprocess",
                    "headless": headless,
                    "interactive_mode": not headless,
                },
            }


class XiaoyuzhouChannelAdapter(BaseChannelAdapter):
    @property
    def name(self) -> str:
        return "xiaoyuzhou"

    def can_handle(self, url: str) -> bool:
        return "xiaoyuzhoufm.com" in self._domain(url)

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        return WebChannelAdapter().fetch(url, config)

    def check(self, config: dict[str, Any]) -> str:
        return "ok"


DEFAULT_CHANNELS = [
    YouTubeChannelAdapter,
    TwitterChannelAdapter,
    RedditChannelAdapter,
    V2EXChannelAdapter,
    HackerNewsChannelAdapter,
    XiaoyuzhouChannelAdapter,
    WechatChannelAdapter,
    RSSChannelAdapter,
    WebChannelAdapter,
]


class AgentReachFetcher:
    """V3 multi-channel fetcher with V3 typed errors."""

    _SHARED_PROXY_FILE = Path.home() / ".100x_v3" / "network_state.json"

    def __init__(
        self,
        config_path: str | None = None,
        enabled_channels: list[str] | None = None,
        fallback_to_jina: bool = True,
        proxy: str | None = None,
        silent: bool = False,
        http_client: HttpClient | None = None,
        wechat_config: WechatConfig | None = None,
    ) -> None:
        self.config_path = Path(os.path.expanduser(config_path)) if config_path else Path.home() / ".agent-reach" / "config.yaml"
        self.fallback_to_jina = fallback_to_jina
        self.enabled_channels = enabled_channels
        self.silent = silent
        self.base_proxy = proxy
        self._last_working_proxy: Any = None
        self.ar_config = self._load_ar_config()
        effective_proxy = proxy or _proxy_from_config(self.ar_config)
        self.http_client = http_client or create_http_client(proxy=effective_proxy or None)
        self.ar_config.setdefault("http_client", self.http_client)
        if effective_proxy:
            self.ar_config["proxy"] = effective_proxy
        # Merge wechat_config into ar_config if provided
        if wechat_config is not None:
            self.ar_config.setdefault("wechat", {}).update({
                "headless_first": wechat_config.headless_first,
                "interactive_on_blocked": wechat_config.interactive_on_blocked,
                "profile_dir": wechat_config.profile_dir,
                "verification_timeout_seconds": wechat_config.verification_timeout_seconds,
            })
        self.channels = self._load_channels()

    def _load_ar_config(self) -> dict[str, Any]:
        if not self.config_path.exists():
            return {}
        try:
            import yaml  # type: ignore[import-untyped]
            loaded = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
            return loaded if isinstance(loaded, dict) else {}
        except Exception:
            return {}

    def _load_channels(self) -> list[BaseChannelAdapter]:
        channels: list[BaseChannelAdapter] = []
        enabled = set(self.enabled_channels or [])
        for channel_cls in DEFAULT_CHANNELS:
            channel = channel_cls()
            if enabled and channel.name not in enabled:
                continue
            channels.append(channel)
        return channels

    def fetch(self, url: str) -> FetchedContent | TypedError:
        url = _normalize_url(url)
        matched = [channel for channel in self.channels if channel.can_handle(url)]
        if self.fallback_to_jina and not any(channel.name == "web" for channel in matched):
            matched.append(WebChannelAdapter())

        proxy_candidates = self._get_proxy_candidates()

        for channel in matched:
            last_error: Exception | None = None
            for p_mode in proxy_candidates:
                # Set proxy on ar_config for this attempt
                self.ar_config["proxy"] = p_mode
                try:
                    result = channel.fetch(url, self.ar_config)
                    content = str(result.get("content", "")).strip() if result else ""
                    if channel.name == "wechat" and content and _is_wechat_verification_page(content):
                        return TypedError(
                            failure_kind=FailureKind.CONTENT_BLOCKED,
                            message="Wechat article requires verification before content can be fetched",
                            stage="fetch",
                            retryable=False,
                            next_action=NextAction.MANUAL_REVIEW,
                            detail=f"url={url}",
                        )
                    if content and not _is_thin_channel_result(channel.name, content):
                        # Success — remember the working proxy
                        if p_mode != self._last_working_proxy:
                            self._last_working_proxy = p_mode
                            self._save_shared_proxy(p_mode)
                        return self._to_fetched_content(result, url, channel.name)
                except Exception as e:
                    last_error = e
                    # If shared proxy failed, invalidate it
                    shared = self._load_shared_proxy()
                    if shared == p_mode:
                        self._save_shared_proxy(None)
                    continue

            if not self.silent and last_error:
                p_str = p_mode.get("https") if isinstance(p_mode, dict) else (p_mode or "Direct/TUN")
                print(f"  ⚠️  Channel {channel.name} failed (all proxy modes) | last: {last_error}")

        return TypedError(
            failure_kind=FailureKind.FETCH_FAILED,
            message="All multi-channel fetch routes failed",
            stage="fetch",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            detail=f"url={url}",
        )

    # -- Proxy auto-detection (ported from V2) --

    def _get_proxy_candidates(self) -> list[Any]:
        """Build ordered list of proxy modes to try."""
        candidates: list[Any] = []

        # 1. Shared memory (cross-session / cross-process)
        shared = self._load_shared_proxy()
        if shared:
            candidates.append(shared)
        if self._last_working_proxy and self._last_working_proxy != shared:
            candidates.append(self._last_working_proxy)

        # 2. Explicit proxy from config or env
        p = self.base_proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if p:
            if not p.startswith("http"):
                p = f"http://{p}"
            mode = {"http": p, "https": p}
            if mode not in candidates:
                candidates.append(mode)

        # 3. Direct / TUN (no proxy)
        if None not in candidates:
            candidates.append(None)

        # 4. Common local proxy ports
        for port in ["7890", "7897", "1080", "1087"]:
            hp = f"http://127.0.0.1:{port}"
            mode = {"http": hp, "https": hp}
            if mode not in candidates:
                candidates.append(mode)

        return candidates

    def _load_shared_proxy(self) -> Any:
        path = self._SHARED_PROXY_FILE
        if not path.exists():
            return None
        try:
            import time as _time
            data = json.loads(path.read_text(encoding="utf-8"))
            if _time.time() - data.get("timestamp", 0) > 3600:
                return None
            return data.get("proxy")
        except Exception:
            return None

    def _save_shared_proxy(self, proxy: Any) -> None:
        path = self._SHARED_PROXY_FILE
        try:
            import time as _time
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"proxy": proxy, "timestamp": _time.time()}))
        except Exception:
            pass

    def health_check(self) -> dict[str, str]:
        return {channel.name: channel.check(self.ar_config) for channel in self.channels}

    def _to_fetched_content(self, result: dict[str, Any], url: str, channel_name: str) -> FetchedContent:
        content = str(result.get("content", ""))
        metadata = result.get("metadata", {})
        if not isinstance(metadata, dict):
            metadata = {}
        metadata.update({
            "fetcher": f"agent_reach_{channel_name}",
            "agent_reach_channel": channel_name,
        })
        return FetchedContent(
            url=url,
            source=str(result.get("source", _source_from_url(url))),
            source_type={
                "youtube": "youtube_video",
                "twitter": "twitter_thread",
                "reddit": "reddit_post",
                "v2ex": "v2ex_discussion",
                "hackernews": "hackernews_thread",
                "wechat": "wechat_article",
                "xiaoyuzhou": "podcast_episode",
                "rss": "rss_feed",
                "web": "web_article",
            }.get(channel_name, "web_article"),
            title=str(result.get("title", "")) or url[:100],
            text=content,
            raw=content,
            author=str(result.get("author", "")),
            published_at=str(result.get("published_at", "")),
            fetched_at=utc_now(),
            content_hash=sha256_text(content),
            metadata=metadata,
        )

def _normalize_url(url: str) -> str:
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _source_from_url(url: str) -> str:
    return urlparse(url).netloc.lower().removeprefix("www.")


def _proxy_from_config(config: dict[str, Any]) -> str:
    proxy = config.get("proxy")
    if isinstance(proxy, dict):
        return str(proxy.get("https") or proxy.get("http") or "")
    return str(proxy or "")


def _proxy_env(config: dict[str, Any]) -> dict[str, str]:
    env = os.environ.copy()
    proxy = _proxy_from_config(config)
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["ALL_PROXY"] = proxy
        env["https_proxy"] = proxy
        env["http_proxy"] = proxy
        env["all_proxy"] = proxy
    return env


def _apply_proxy_env(config: dict[str, Any]) -> dict[str, str | None]:
    keys = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
    previous = {key: os.environ.get(key) for key in keys}
    proxy = _proxy_from_config(config)
    if proxy:
        for key in keys:
            os.environ[key] = proxy
    return previous


def _restore_proxy_env(previous: dict[str, str | None]) -> None:
    for key, value in previous.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _title_from_wechat_markdown(content: str) -> str:
    for line in content.splitlines()[:20]:
        if line.lower().startswith("title:"):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def _is_wechat_verification_page(content: str) -> bool:
    if "环境异常" in content or "验证后即可继续访问" in content:
        return True
    header = content[:4000].lower()
    return "captcha" in header or "verification page" in header


def _title_from_jina(content: str) -> str:
    for line in content.splitlines():
        if line.startswith("Title: "):
            return line.removeprefix("Title: ").strip()
    return ""


def _title_from_html(html: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _body_from_html(html: str) -> str:
    m = re.search(r"<body[^>]*>(.*)</body>", html, re.IGNORECASE | re.DOTALL)
    raw = m.group(1) if m else html
    return re.sub(r"<[^>]+>", " ", raw).strip()


def _body_from_jina(content: str) -> str:
    marker = "Markdown Content:"
    if marker in content:
        return content.split(marker, 1)[1].strip()
    return content.strip()


def _is_thin_channel_result(channel_name: str, content: str) -> bool:
    if channel_name != "twitter":
        return False
    if re.fullmatch(r"https?://t\.co/\S+", content.strip()):
        return True
    return len(content.strip()) < 80 and "t.co/" in content


def fetch(url: str, config_path: str | None = None) -> FetchedContent | TypedError:
    return AgentReachFetcher(config_path=config_path).fetch(url)


def check_health(config_path: str | None = None) -> dict[str, str]:
    return AgentReachFetcher(config_path=config_path).health_check()
