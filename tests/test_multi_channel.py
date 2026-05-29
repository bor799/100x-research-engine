from pathlib import Path

from knowledge_extractor_v3.fetchers.multi_channel import (
    AgentReachFetcher,
    TwitterChannelAdapter,
    WechatChannelAdapter,
    YouTubeChannelAdapter,
)
from knowledge_extractor_v3.models import TypedError
from knowledge_extractor_v3.queue_store import FailureKind


def _write_executable(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)
    return path


def test_twitter_health_reports_missing_config_when_auth_check_fails(tmp_path):
    xreach = _write_executable(
        tmp_path / "xreach",
        "#!/bin/sh\nexit 1\n",
    )

    assert TwitterChannelAdapter().check({"xreach_path": str(xreach)}) == "missing_config"


def test_twitter_health_accepts_configured_tokens(tmp_path):
    xreach = _write_executable(
        tmp_path / "xreach",
        "#!/bin/sh\nexit 1\n",
    )

    result = TwitterChannelAdapter().check({
        "xreach_path": str(xreach),
        "twitter": {
            "auth_token": "token",
            "ct0": "csrf",
        },
    })

    assert result == "ok"


def test_twitter_fetch_passes_proxy_flag(tmp_path):
    args_file = tmp_path / "xreach.args"
    xreach = _write_executable(
        tmp_path / "xreach",
        "\n".join([
            "#!/bin/sh",
            f"printf '%s\\n' \"$@\" > '{args_file}'",
            "cat <<'JSON'",
            '{"text":"tweet body","user":{"screenName":"dotey"},"createdAt":"Tue May 05 15:39:53 +0000 2026"}',
            "JSON",
        ]),
    )

    result = TwitterChannelAdapter().fetch(
        "https://x.com/dotey/status/1",
        {
            "xreach_path": str(xreach),
            "twitter": {
                "auth_token": "token",
                "ct0": "csrf",
            },
            "proxy": "http://127.0.0.1:7897",
        },
    )

    assert result is not None
    assert result["content"] == "tweet body"
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--proxy" in args
    assert "http://127.0.0.1:7897" in args


def test_youtube_fetch_passes_optional_cookie_and_proxy_flags(tmp_path):
    args_file = tmp_path / "yt-dlp.args"
    yt_dlp = _write_executable(
        tmp_path / "yt-dlp",
        "\n".join([
            "#!/bin/sh",
            f"printf '%s\\n' \"$@\" > '{args_file}'",
            "cat <<'JSON'",
            '{"title":"Video Title","description":"Video body","uploader":"Channel","upload_date":"20260430","id":"abc","duration":60}',
            "JSON",
        ]),
    )

    result = YouTubeChannelAdapter().fetch(
        "https://youtu.be/abc",
        {
            "yt_dlp_path": str(yt_dlp),
            "youtube": {
                "cookies_from_browser": "chrome",
                "proxy": "http://127.0.0.1:7890",
            },
        },
    )

    assert result is not None
    assert result["title"] == "Video Title"
    args = args_file.read_text(encoding="utf-8").splitlines()
    assert "--cookies-from-browser" in args
    assert "chrome" in args
    assert "--proxy" in args
    assert "http://127.0.0.1:7890" in args


def test_wechat_fetch_uses_agent_reach_tool_subprocess(tmp_path):
    tool = tmp_path / "wechat-article-for-ai"
    tool.mkdir()
    main_py = _write_executable(
        tool / "main.py",
        "\n".join([
            "from pathlib import Path",
            "import argparse",
            "parser = argparse.ArgumentParser()",
            "parser.add_argument('url')",
            "parser.add_argument('--no-images', action='store_true')",
            "parser.add_argument('--force', action='store_true')",
            "parser.add_argument('-o', '--output', type=Path, default=Path('output'))",
            "args = parser.parse_args()",
            "article_dir = args.output / 'Agent Wechat Title'",
            "article_dir.mkdir(parents=True, exist_ok=True)",
            "(article_dir / 'Agent Wechat Title.md').write_text(",
            "    '---\\ntitle: Agent Wechat Title\\n---\\n\\nwechat body from agent reach',",
            "    encoding='utf-8',",
            ")",
        ]),
    )
    assert main_py.exists()

    result = WechatChannelAdapter().fetch(
        "https://mp.weixin.qq.com/s/example",
        {"wechat": {"tool_path": str(tool)}},
    )

    assert result is not None
    assert result["source"] == "agent-reach-wechat"
    assert result["title"] == "Agent Wechat Title"
    assert "wechat body from agent reach" in result["content"]
    assert result["metadata"]["agent_reach_tool"] == "wechat-article-for-ai"


def test_agent_reach_configures_web_http_client_proxy(tmp_path):
    fetcher = AgentReachFetcher(
        config_path=str(tmp_path / "missing.yaml"),
        proxy="http://127.0.0.1:7897",
    )

    assert fetcher.http_client.proxy == "http://127.0.0.1:7897"
    assert fetcher.ar_config["proxy"] == "http://127.0.0.1:7897"


class FailingSpecialChannel:
    name = "twitter"

    def can_handle(self, url: str) -> bool:
        return "x.com" in url

    def fetch(self, url: str, config: dict):
        return None

    def check(self, config: dict) -> str:
        return "ok"


class SuccessfulWebChannel:
    name = "web"

    def can_handle(self, url: str) -> bool:
        return url.startswith("http")

    def fetch(self, url: str, config: dict):
        return {
            "title": "Fallback title",
            "content": "Fallback content from Jina",
            "source": "agent-reach-web",
            "metadata": {"via_jina": True},
        }

    def check(self, config: dict) -> str:
        return "ok"


def test_agent_reach_tries_web_fallback_after_special_channel_failure(tmp_path):
    fetcher = AgentReachFetcher(config_path=str(tmp_path / "missing.yaml"), fallback_to_jina=False)
    fetcher.channels = [FailingSpecialChannel(), SuccessfulWebChannel()]  # type: ignore[list-item]

    result = fetcher.fetch("https://x.com/example/status/1")

    assert result.source == "agent-reach-web"
    assert result.metadata["agent_reach_channel"] == "web"


class ThinTwitterChannel:
    name = "twitter"

    def can_handle(self, url: str) -> bool:
        return "x.com" in url

    def fetch(self, url: str, config: dict):
        return {
            "title": "@dotey",
            "content": "https://t.co/example",
            "source": "agent-reach-twitter",
        }

    def check(self, config: dict) -> str:
        return "ok"


def test_agent_reach_skips_tco_only_twitter_result_for_fallback(tmp_path):
    fetcher = AgentReachFetcher(config_path=str(tmp_path / "missing.yaml"), fallback_to_jina=False)
    fetcher.channels = [ThinTwitterChannel(), SuccessfulWebChannel()]  # type: ignore[list-item]

    result = fetcher.fetch("https://x.com/dotey/status/1")

    assert result.source == "agent-reach-web"
    assert result.text == "Fallback content from Jina"


class VerificationWechatChannel:
    name = "wechat"

    def can_handle(self, url: str) -> bool:
        return "mp.weixin.qq.com" in url

    def fetch(self, url: str, config: dict):
        return {
            "title": "Weixin Official Accounts Platform",
            "content": "## 环境异常\n\n当前环境异常，完成验证后即可继续访问。\n\n去验证",
            "source": "agent-reach-wechat",
        }

    def check(self, config: dict) -> str:
        return "ok"


def test_agent_reach_reports_wechat_verification_as_content_blocked(tmp_path):
    fetcher = AgentReachFetcher(config_path=str(tmp_path / "missing.yaml"), fallback_to_jina=False)
    fetcher.channels = [VerificationWechatChannel()]  # type: ignore[list-item]

    result = fetcher.fetch("https://mp.weixin.qq.com/s/example")

    assert isinstance(result, TypedError)
    assert result.failure_kind is FailureKind.CONTENT_BLOCKED
