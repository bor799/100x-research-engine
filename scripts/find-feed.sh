#!/usr/bin/env bash
# 快速 RSS/Atom feed 发现 — 在决定写抓取代码之前，先跑这个。
# 用法: ./scripts/find-feed.sh https://example.com
#
# 检查顺序:
#   1. HTML <link rel="alternate"> 标签
#   2. 14 个常见 feed 路径 (404 跳过，200+XML 即命中)
#
# 教训: elsewhere.news 有 /feed.xml 但没在 <head> 声明，
#       导致我们先写了 80 行 WebDiscoveryAdapter 才发现是白费。

set -euo pipefail
URL="${1:?用法: $0 <site-url>}"
# Strip protocol and trailing slash using bash builtins (sed \? is unreliable across platforms)
HOST="${URL#https://}"
HOST="${HOST#http://}"
HOST="${HOST%/}"

echo "🔍 Checking $URL for RSS/Atom feeds..."
echo ""

# 1. Check HTML <link> tags
echo "─ Step 1: HTML <link> discovery"
LINKS=$(curl -sL --max-time 8 "$URL" 2>/dev/null | \
  grep -oiE '<link[^>]*type="application/(rss|atom|rdf)\+xml"[^>]*>' || true)
if [ -n "$LINKS" ]; then
  echo "$LINKS" | grep -oE 'href="[^"]*"' | head -3
else
  echo "  (no <link rel='alternate'> feed declaration found)"
fi
echo ""

# 2. Check common paths
echo "─ Step 2: Common feed paths"
PATHS=(
  "/feed" "/feed/" "/feed.xml" "/feed/rss/" "/feed/atom/"
  "/rss" "/rss/" "/rss.xml"
  "/atom.xml" "/index.xml"
  "/feeds/posts/default" "/blog/feed" "/blog/rss"
  "/?feed=rss2"
)

FOUND=0
for P in "${PATHS[@]}"; do
  FULL="https://$HOST$P"
  # Only show hits: HTTP 200 + XML-like content
  CODE=$(curl -sL --max-time 5 -o /tmp/_feed_check -w "%{http_code}" "$FULL" 2>/dev/null || true)
  if [ "$CODE" = "200" ]; then
    HEAD=$(head -c 200 /tmp/_feed_check 2>/dev/null || true)
    if echo "$HEAD" | grep -qiE '<rss|<feed|xml version'; then
      echo "  ✅ FOUND: $FULL"
      FOUND=$((FOUND + 1))
    fi
  fi
done

if [ "$FOUND" -eq 0 ]; then
  echo "  ❌ No feeds found at common paths."
  echo "  → Consider: RSSHub (https://docs.rsshub.app), web_discovery adapter, or direct scraping."
fi

rm -f /tmp/_feed_check
