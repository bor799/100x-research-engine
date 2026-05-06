from pathlib import Path

from knowledge_extractor_v3.fetchers.multi_channel import (
    TwitterChannelAdapter,
    YouTubeChannelAdapter,
)


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
