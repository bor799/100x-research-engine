#!/usr/bin/env python3
"""Score-only traceback for a URL: fetch, preprocess, score, dump full JSON.

Reproduces the exact scoring path used by the worker (Pipeline._score_and_parse)
without touching the queue, outputs, or WeChat. Used to audit why items land on
a given final_score and route, and to compare prompt bundles offline.

Usage:
    python scripts/score_traceback.py <url> [--bundle NAME]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

project_root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(project_root / "src"))

from knowledge_extractor_v3.config_loader import ConfigLoader
from knowledge_extractor_v3.fetchers.router import FetcherRouter
from knowledge_extractor_v3.llm.live_provider import create_live_provider
from knowledge_extractor_v3.models import FetchedContent, TypedError
from knowledge_extractor_v3.pipeline import _preprocess_long_content
from knowledge_extractor_v3.prompt_parser import parse_score_result
from knowledge_extractor_v3.prompt_registry import PromptRegistry
from knowledge_extractor_v3.routing import route_from_score

LONG_CONTENT_THRESHOLD = 10000


def _content_from_html_file(path: Path, url: str) -> FetchedContent:
    """Mirror AgentReachFetcher._to_fetched_content for a locally fetched page."""
    from knowledge_extractor_v3.models import sha256_text, utc_now

    html = path.read_text(encoding="utf-8")
    title_match = re.search(r'<meta[^>]+property="og:title"[^>]+content="([^"]+)"', html)
    if not title_match:
        title_match = re.search(r"<title>(.*?)</title>", html, re.S)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", "\n", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    text = re.sub(r"[ \t]+", " ", text).strip()
    return FetchedContent(
        url=url,
        source=urlparse(url).netloc.lower().removeprefix("www."),
        source_type="web_article",
        title=(title_match.group(1).strip() if title_match else url[:100]),
        text=text,
        fetched_at=utc_now(),
        content_hash=sha256_text(text),
        metadata={"fetcher": "score_traceback_file"},
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Score-only traceback for one URL")
    parser.add_argument("url", help="URL to score")
    parser.add_argument("--bundle", default=None, help="Prompt bundle (default: active)")
    parser.add_argument(
        "--from-file",
        type=Path,
        default=None,
        help="Raw HTML or plain-text file to score instead of fetching (mirror of worker metadata)",
    )
    args = parser.parse_args()

    loader = ConfigLoader(project_root=project_root)
    config = loader.load()
    prompts = PromptRegistry.from_config(project_root, config.prompts)
    bundle_name = args.bundle or prompts.active_bundle_name
    scoring_prompt = prompts.load_prompt(bundle_name, "scoring")

    print(f"bundle={bundle_name} model={config.llm.scoring_model}")
    if args.from_file:
        if args.from_file.suffix == ".html":
            content = _content_from_html_file(args.from_file, args.url)
        else:
            from knowledge_extractor_v3.models import sha256_text, utc_now

            text = args.from_file.read_text(encoding="utf-8").strip()
            content = FetchedContent(
                url=args.url,
                source=urlparse(args.url).netloc.lower().removeprefix("www."),
                source_type="web_article",
                title=args.url[:100],
                text=text,
                fetched_at=utc_now(),
                content_hash=sha256_text(text),
                metadata={"fetcher": "score_traceback_file"},
            )
    else:
        fetcher = FetcherRouter()
        content = fetcher.fetch(args.url)
    if isinstance(content, TypedError):
        print(f"FETCH FAILED: {content.message}")
        return 1

    original_length = len(content.text)
    if original_length > LONG_CONTENT_THRESHOLD:
        content = replace(content, text=_preprocess_long_content(content.text))
    print(
        f"source={content.source!r} title={content.title[:60]!r} "
        f"chars={original_length}->{len(content.text)}"
    )

    llm = create_live_provider(config.llm, env=os.environ)
    raw = llm.score(content, scoring_prompt)
    if isinstance(raw, TypedError):
        print(f"SCORE FAILED: {raw.message} ({raw.detail})")
        return 1

    score = parse_score_result(
        raw,
        prompt_bundle=bundle_name,
        prompt_hash="traceback",
        model_route=getattr(llm, "model_route", "unknown"),
    )
    if isinstance(score, TypedError):
        print(f"PARSE FAILED: {score.message} ({score.detail})")
        print(raw[:2000])
        return 1

    print(json.dumps(score.parsed, ensure_ascii=False, indent=2))
    decision = route_from_score(score)
    print(f"\nROUTE: {decision.route.value} | bsf={decision.business_story_fit:.2f} | {decision.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
