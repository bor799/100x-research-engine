"""Shadow-only public web page fetcher for Phase 3 real URL runs."""

from __future__ import annotations

import re
import socket
import subprocess
from html import unescape
from html.parser import HTMLParser
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from ..models import FetchedContent, TypedError, retry_at, sha256_text, utc_now
from ..queue_store import FailureKind, NextAction


DEFAULT_USER_AGENT = (
    "100x-knowledge-extractor-v3-shadow/0.1 "
    "(public URL validation; no cookies; no logged-in browser state)"
)

PAYWALL_MARKERS = (
    "already a subscriber",
    "become a subscriber",
    "sign in to continue",
    "subscribe to continue",
    "subscription required",
    "start your free trial",
    "to keep reading",
    "unlock this article",
    "you have reached your free article limit",
)


class WebPageFetcher:
    """Fetch a single public URL without using cookies, browser state, or V2 auth."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20,
        max_bytes: int = 2_000_000,
        user_agent: str = DEFAULT_USER_AGENT,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes
        self.user_agent = user_agent

    def fetch(self, url: str) -> FetchedContent | TypedError:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return TypedError(
                failure_kind=FailureKind.VALIDATION_FAILED,
                message="WebPageFetcher only accepts absolute HTTP(S) URLs",
                stage="fetch",
                retryable=False,
                next_action=NextAction.DROP,
                detail=url,
            )

        request = Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.1",
            },
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_response = _RawResponse(
                    status=getattr(response, "status", response.getcode()),
                    content_type=response.headers.get("Content-Type", ""),
                    charset=response.headers.get_content_charset() or "utf-8",
                    body=response.read(self.max_bytes + 1),
                )
        except HTTPError as exc:
            return _http_error(exc, url)
        except TimeoutError:
            return _timeout_error(url)
        except socket.timeout:
            return _timeout_error(url)
        except URLError as exc:
            if isinstance(exc.reason, TimeoutError | socket.timeout):
                return _timeout_error(url)
            fallback = self._fetch_with_curl(url)
            if fallback is not None:
                if isinstance(fallback, TypedError):
                    return fallback
                raw_response = fallback
            else:
                return TypedError(
                    failure_kind=FailureKind.FETCH_FAILED,
                    message="URL fetch failed",
                    stage="fetch",
                    retryable=False,
                    next_action=NextAction.MANUAL_REVIEW,
                    detail=str(exc.reason),
                )
        except OSError as exc:
            fallback = self._fetch_with_curl(url)
            if fallback is not None:
                if isinstance(fallback, TypedError):
                    return fallback
                raw_response = fallback
            else:
                return TypedError(
                    failure_kind=FailureKind.FETCH_FAILED,
                    message="URL fetch failed",
                    stage="fetch",
                    retryable=False,
                    next_action=NextAction.MANUAL_REVIEW,
                    detail=str(exc),
                )

        if raw_response.status >= 400:
            return _status_error(raw_response.status, url)

        raw_bytes = raw_response.body[: self.max_bytes]
        raw = raw_bytes.decode(raw_response.charset, errors="replace")
        if _unsupported_content_type(raw_response.content_type, raw):
            return TypedError(
                failure_kind=FailureKind.VALIDATION_FAILED,
                message="Fetched response is not readable HTML/text",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=f"{url} content_type={raw_response.content_type}",
            )

        page = extract_page(raw, fallback_title=parsed.netloc)
        title = page.title
        text = page.text
        if _looks_content_blocked(parsed.netloc, title, text):
            return TypedError(
                failure_kind=FailureKind.CONTENT_BLOCKED,
                message="Fetched page appears to be paywalled or content-blocked",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=url,
            )

        if len(text.split()) < 80:
            return TypedError(
                failure_kind=FailureKind.VALIDATION_FAILED,
                message="Extracted page body is too short",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=f"{url} words={len(text.split())}",
            )

        return FetchedContent(
            url=url,
            source=_source_from_url(url),
            source_type="web_article",
            title=title,
            text=text,
            raw=raw,
            author=page.author,
            published_at=page.published_at,
            fetched_at=utc_now(),
            content_hash=sha256_text(text),
            metadata={
                "fetcher": "web_page_shadow",
                "content_type": raw_response.content_type,
                "http_status": raw_response.status,
                "word_count": len(text.split()),
                "description": page.description,
            },
        )

    def _fetch_with_curl(self, url: str) -> _RawResponse | TypedError | None:
        marker = b"\n__100X_STATUS__:"
        content_type_marker = b"\n__100X_CONTENT_TYPE__:"
        command = [
            "curl",
            "-L",
            "--max-time",
            str(int(self.timeout_seconds)),
            "--silent",
            "--show-error",
            "--user-agent",
            self.user_agent,
            "--write-out",
            "\n__100X_STATUS__:%{http_code}\n__100X_CONTENT_TYPE__:%{content_type}\n",
            url,
        ]
        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                timeout=self.timeout_seconds + 5,
                check=False,
            )
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            return _timeout_error(url)

        body, found, trailer = completed.stdout.rpartition(marker)
        if not found:
            if completed.returncode == 28:
                return _timeout_error(url)
            if completed.returncode != 0:
                return TypedError(
                    failure_kind=FailureKind.FETCH_FAILED,
                    message="curl fallback failed",
                    stage="fetch",
                    retryable=False,
                    next_action=NextAction.MANUAL_REVIEW,
                    detail=completed.stderr.decode("utf-8", errors="replace").strip(),
                )
            return None

        status_bytes, _, content_type_bytes = trailer.partition(content_type_marker)
        try:
            status = int(status_bytes.strip() or b"0")
        except ValueError:
            status = 0
        if status == 0 and completed.returncode != 0:
            return TypedError(
                failure_kind=FailureKind.FETCH_FAILED,
                message="curl fallback failed",
                stage="fetch",
                retryable=False,
                next_action=NextAction.MANUAL_REVIEW,
                detail=completed.stderr.decode("utf-8", errors="replace").strip(),
            )
        return _RawResponse(
            status=status,
            content_type=content_type_bytes.decode("utf-8", errors="replace").strip(),
            charset="utf-8",
            body=body,
        )


class ExtractedPage:
    def __init__(
        self,
        *,
        title: str,
        text: str,
        description: str = "",
        author: str = "",
        published_at: str = "",
    ) -> None:
        self.title = title
        self.text = text
        self.description = description
        self.author = author
        self.published_at = published_at


class _RawResponse:
    def __init__(self, *, status: int, content_type: str, charset: str, body: bytes) -> None:
        self.status = status
        self.content_type = content_type
        self.charset = charset
        self.body = body


class _HTMLTextExtractor(HTMLParser):
    block_tags = {
        "address",
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "figcaption",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "td",
        "th",
        "tr",
    }
    skip_tags = {"script", "style", "noscript", "svg", "canvas", "form"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self.meta: dict[str, str] = {}
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_dict = {name.lower(): value or "" for name, value in attrs}
        if tag in self.skip_tags:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            key = attrs_dict.get("property") or attrs_dict.get("name")
            content = attrs_dict.get("content", "").strip()
            if key and content:
                self.meta[key.lower()] = unescape(content)
        if tag in self.block_tags and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in self.skip_tags and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.block_tags and self._skip_depth == 0:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
            return
        if self._skip_depth:
            return
        self.text_parts.append(data)

    @property
    def title(self) -> str:
        return _normalize_inline(" ".join(self.title_parts))

    @property
    def text(self) -> str:
        return _normalize_text("".join(self.text_parts))


def extract_page(html: str, *, fallback_title: str = "Untitled") -> ExtractedPage:
    """Extract title, metadata, and a readable body from HTML."""

    full = _parse_html(html)
    article_candidates = [_parse_html(candidate) for candidate in _article_html_candidates(html)]
    page = max([full, *article_candidates], key=lambda item: len(item.text.split()))
    title = (
        page.meta.get("og:title")
        or page.meta.get("twitter:title")
        or full.meta.get("og:title")
        or full.meta.get("twitter:title")
        or full.title
        or fallback_title
    )
    return ExtractedPage(
        title=_clean_title(title, fallback=fallback_title),
        text=page.text or full.text,
        description=page.meta.get("description") or full.meta.get("description", ""),
        author=page.meta.get("author") or full.meta.get("author", ""),
        published_at=(
            page.meta.get("article:published_time")
            or page.meta.get("pubdate")
            or full.meta.get("article:published_time")
            or full.meta.get("pubdate", "")
        ),
    )


def _parse_html(html: str) -> _HTMLTextExtractor:
    parser = _HTMLTextExtractor()
    parser.feed(html)
    parser.close()
    return parser


def _article_html_candidates(html: str) -> list[str]:
    candidates: list[str] = []
    patterns = (
        r"<article\b[^>]*>.*?</article>",
        r"<main\b[^>]*>.*?</main>",
        r"<section\b[^>]*(?:article|story|post|entry|content)[^>]*>.*?</section>",
        r"<div\b[^>]*(?:article|story|post|entry-content|article-content|post-content)[^>]*>.*?</div>",
    )
    for pattern in patterns:
        candidates.extend(re.findall(pattern, html, flags=re.IGNORECASE | re.DOTALL))
    return candidates[:20]


def _normalize_text(text: str) -> str:
    lines = []
    previous = ""
    for raw_line in text.splitlines():
        line = _normalize_inline(raw_line)
        if not line or line == previous:
            continue
        lower = line.lower()
        if lower in {"advertisement", "skip to content", "share this article"}:
            continue
        lines.append(line)
        previous = line
    return "\n".join(lines).strip()


def _normalize_inline(text: str) -> str:
    return re.sub(r"\s+", " ", unescape(text)).strip()


def _clean_title(title: str, *, fallback: str) -> str:
    cleaned = _normalize_inline(title)
    cleaned = re.split(r"\s+[|-]\s+", cleaned)[0].strip()
    return cleaned or fallback


def _unsupported_content_type(content_type: str, raw: str) -> bool:
    lower = content_type.lower()
    if not lower:
        return not re.search(r"<html|<article|<body|<p\b", raw, flags=re.IGNORECASE)
    return not (
        "text/html" in lower
        or "application/xhtml" in lower
        or "text/plain" in lower
        or "charset=" in lower
    )


def _looks_content_blocked(host: str, title: str, text: str) -> bool:
    words = len(text.split())
    lower = f"{title}\n{text}".lower()
    has_marker = any(marker in lower for marker in PAYWALL_MARKERS)
    if "theinformation.com" in host.lower():
        return has_marker or words < 350
    return has_marker and words < 450


def _source_from_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host.removeprefix("www.")


def _timeout_error(url: str) -> TypedError:
    return TypedError(
        failure_kind=FailureKind.FETCH_TIMEOUT,
        message="URL fetch timed out",
        stage="fetch",
        retryable=True,
        next_action=NextAction.RETRY_LATER,
        detail=url,
        next_retry_at=retry_at(15),
    )


def _http_error(exc: HTTPError, url: str) -> TypedError:
    return _status_error(exc.code, url, detail=str(exc))


def _status_error(status: int, url: str, *, detail: str = "") -> TypedError:
    if status == 401:
        return TypedError(
            failure_kind=FailureKind.AUTH_INVALID,
            message="URL requires authentication",
            stage="fetch",
            retryable=False,
            next_action=NextAction.AUTH_REFRESH_REQUIRED,
            detail=detail or url,
        )
    if status == 403:
        return TypedError(
            failure_kind=FailureKind.CONTENT_BLOCKED,
            message="URL returned HTTP 403 content-blocked response",
            stage="fetch",
            retryable=False,
            next_action=NextAction.MANUAL_REVIEW,
            detail=detail or url,
        )
    if status in {408, 429, 500, 502, 503, 504}:
        return TypedError(
            failure_kind=FailureKind.FETCH_FAILED,
            message=f"URL returned retryable HTTP {status}",
            stage="fetch",
            retryable=True,
            next_action=NextAction.RETRY_LATER,
            detail=detail or url,
            next_retry_at=retry_at(15),
        )
    return TypedError(
        failure_kind=FailureKind.FETCH_FAILED,
        message=f"URL returned HTTP {status}",
        stage="fetch",
        retryable=False,
        next_action=NextAction.MANUAL_REVIEW,
        detail=detail or url,
    )
