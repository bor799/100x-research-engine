#!/bin/bash
# Agent-Reach 配置测试和设置脚本

set -e

V3_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
AR_CONFIG="$HOME/.agent-reach/config.yaml"

echo "==================================="
echo "Agent-Reach 配置检查"
echo "==================================="
echo ""

# 检查 xreach
echo "1. 检查 xreach..."
if command -v xreach &> /dev/null; then
    XREACH_PATH=$(command -v xreach)
    echo "   ✓ xreach 已安装: $XREACH_PATH"
    xreach --version | head -1
else
    echo "   ✗ xreach 未安装"
    echo "   安装: npm install -g xreach"
fi

# 检查 yt-dlp
echo ""
echo "2. 检查 yt-dlp..."
if command -v yt-dlp &> /dev/null; then
    YT_DLP_PATH=$(command -v yt-dlp)
    echo "   ✓ yt-dlp 已安装: $YT_DLP_PATH"
    yt-dlp --version | head -1
else
    echo "   ✗ yt-dlp 未安装"
    echo "   安装: brew install yt-dlp"
    echo "   或: pip install yt-dlp"
fi

# 检查认证状态
echo ""
echo "3. 检查 Twitter 认证..."
if xreach auth check &> /dev/null; then
    echo "   ✓ Twitter 已认证"
else
    echo "   ⚠ Twitter 未认证"
    echo ""
    echo "   设置认证（从浏览器提取）:"
    echo "   xreach auth extract --browser chrome"
    echo ""
    echo "   或手动设置:"
    echo "   1. 打开 twitter.com，登录"
    echo "   2. 打开开发者工具 -> Application -> Local Storage"
    echo "   3. 复制 auth_token 和 ct0 的值"
    echo "   4. 运行: xreach auth set --auth-token <token> --ct0 <token>"
fi

# 测试 YouTube
echo ""
echo "4. 测试 YouTube 抓取..."
if command -v yt-dlp &> /dev/null; then
    echo "   测试 URL: https://www.youtube.com/watch?v=Yvl7M3gYAB8"
    if timeout 30 yt-dlp --dump-json --skip-download --no-warnings "https://www.youtube.com/watch?v=Yvl7M3gYAB8" &> /dev/null; then
        echo "   ✓ YouTube 抓取正常"
    else
        echo "   ⚠ YouTube 可能需要代理或 cookies"
        echo "   设置代理: export HTTPS_PROXY=http://127.0.0.1:7890"
        echo "   导出 cookies: yt-dlp --cookies-from-browser chrome URL"
    fi
fi

# 显示配置路径
echo ""
echo "==================================="
echo "配置文件路径"
echo "==================================="
echo "Agent-Reach: $AR_CONFIG"
echo "V3 配置: $V3_ROOT/config/config.local.yaml"
echo ""

# 创建/更新配置
echo "建议配置 (~/.agent-reach/config.yaml):"
echo "---"
cat << YAML
# Twitter/X (xreach)
twitter:
  auth_token: ""  # 从浏览器提取或留空
  ct0: ""

# 代理（留空使用 TUN 或自动探测）
proxy: ""

# 工具路径（如果不在 PATH 中）
xreach_path: "$XREACH_PATH"
yt_dlp_path: "$YT_DLP_PATH"
YAML
echo "---"
