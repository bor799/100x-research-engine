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
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..models import FetchedContent, TypedError, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction
from .http_client import HttpClient, create_http_client
from .rss_channel import RSSChannelAdapter
from .social import RedditChannelAdapter, V2EXChannelAdapter, HackerNewsChannelAdapter


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
        response = client.get_via_jina(url)
        if isinstance(response, TypedError) or not response.is_success:
            return None
        return {
            "title": _title_from_jina(response.content) or url[:100],
            "content": _body_from_jina(response.content),
            "source": "agent-reach-web",
            "metadata": {"via_jina": True},
        }

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

    @property
    def name(self) -> str:
        return "wechat"

    def can_handle(self, url: str) -> bool:
        return self._domain(url) == "mp.weixin.qq.com"

    def fetch(self, url: str, config: dict[str, Any]) -> dict[str, Any] | None:
        tool_path = self._tool_path(config)
        if tool_path.exists():
            result = self._fetch_in_process(url, config, tool_path)
            if result:
                return result

            result = self._fetch_subprocess(url, config, tool_path)
            if result:
                return result

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

    def _fetch_in_process(self, url: str, config: dict[str, Any], tool_path: Path) -> dict[str, Any] | None:
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

            html = asyncio.run(fetch_page_html(url, headless=True))
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
                },
            }
        except Exception:
            return None
        finally:
            _restore_proxy_env(previous_env)
            if added and tool_str in sys.path:
                sys.path.remove(tool_str)

    def _fetch_subprocess(self, url: str, config: dict[str, Any], tool_path: Path) -> dict[str, Any] | None:
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
            try:
                completed = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=120,
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

    def __init__(
        self,
        config_path: str | None = None,
        enabled_channels: list[str] | None = None,
        fallback_to_jina: bool = True,
        proxy: str | None = None,
        silent: bool = False,
        http_client: HttpClient | None = None,
    ) -> None:
        self.config_path = Path(os.path.expanduser(config_path)) if config_path else Path.home() / ".agent-reach" / "config.yaml"
        self.fallback_to_jina = fallback_to_jina
        self.enabled_channels = enabled_channels
        self.silent = silent
        self.base_proxy = proxy
        self.ar_config = self._load_ar_config()
        effective_proxy = proxy or _proxy_from_config(self.ar_config)
        self.http_client = http_client or create_http_client(proxy=effective_proxy or None)
        self.ar_config.setdefault("http_client", self.http_client)
        if effective_proxy:
            self.ar_config["proxy"] = effective_proxy
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

        for channel in matched:
            result = channel.fetch(url, self.ar_config)
            content = str(result.get("content", "")).strip() if result else ""
            if content and not _is_thin_channel_result(channel.name, content):
                return self._to_fetched_content(result, url, channel.name)

        return TypedError(
            failure_kind=FailureKind.FETCH_FAILED,
            message="All multi-channel fetch routes failed",
            stage="fetch",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            detail=f"url={url}",
        )

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
