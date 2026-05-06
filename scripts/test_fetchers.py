#!/usr/bin/env python3
"""测试 V3 Fetcher 配置"""

import sys
from pathlib import Path

# 添加 V3 到路径
v3_root = Path(__file__).parent.parent
sys.path.insert(0, str(v3_root / "src"))

from knowledge_extractor_v3.fetchers.multi_channel import AgentReachFetcher

def test_twitter():
    """测试 Twitter 抓取"""
    print("=" * 50)
    print("测试 Twitter/X 抓取")
    print("=" * 50)
    
    fetcher = AgentReachFetcher()
    status = fetcher.health_check()
    
    print(f"Twitter 状态: {status.get('twitter', 'unknown')}")
    
    if status.get('twitter') == 'not_installed':
        print("  ✗ xreach 未安装")
        return False
    elif status.get('twitter') == 'missing_config':
        print("  ✗ Twitter 缺少认证配置")
        return False
    
    print("  ✓ 配置正常")
    return True

def test_youtube():
    """测试 YouTube 抓取"""
    print("\n" + "=" * 50)
    print("测试 YouTube 抓取")
    print("=" * 50)
    
    fetcher = AgentReachFetcher()
    status = fetcher.health_check()
    
    print(f"YouTube 状态: {status.get('youtube', 'unknown')}")
    
    if status.get('youtube') == 'not_installed':
        print("  ✗ yt-dlp 未安装")
        return False
    
    print("  ✓ 配置正常")
    return True

def test_all_channels():
    """测试所有通道"""
    print("\n" + "=" * 50)
    print("所有通道状态")
    print("=" * 50)
    
    fetcher = AgentReachFetcher()
    status = fetcher.health_check()
    
    for channel, state in status.items():
        symbol = "✓" if state == "ok" else "✗"
        print(f"  {symbol} {channel}: {state}")
    
    return all(s == "ok" for s in status.values())

if __name__ == "__main__":
    test_twitter()
    test_youtube()
    test_all_channels()
