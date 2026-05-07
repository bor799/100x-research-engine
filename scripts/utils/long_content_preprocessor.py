#!/usr/bin/env python3
"""
长文章预处理器 - 智能压缩和分段

用于解决 LLM 处理长微信文章时的超时问题
"""

import re
from typing import Tuple

def preprocess_wechat_article(text: str, max_length: int = 8000) -> str:
    """
    预处理微信文章，智能压缩到合适长度

    Args:
        text: 原始文章文本
        max_length: 最大长度限制（默认 8000 字符）

    Returns:
        压缩后的文章文本
    """
    # 1. 移除常见的噪音内容
    noise_patterns = [
        r'点击.*?关注.*?',
        r'扫码.*?关注.*?',
        r'转载.*?授权.*?',
        r'本文.*?版权.*?',
        r'更多精彩.*?关注.*?',
        r'欢迎.*?订阅.*?',
        r'[^a-zA-Z0-9\u4e00-\u9fff\s]{10,}',  # 移除长串特殊字符
    ]

    for pattern in noise_patterns:
        text = re.sub(pattern, '', text, flags=re.IGNORECASE)

    # 2. 保留高质量内容段落
    paragraphs = text.split('\n')
    high_quality_paragraphs = []

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        # 跳过明显噪音段落
        if any(skip_word in para.lower() for skip_word in [
            '点击关注', '扫码关注', '转载请注明', '版权声明',
            '商务合作', '投稿', '广告', '更多精彩', '推荐阅读'
        ]):
            continue

        # 保留有实质内容的段落（至少 20 个字符）
        if len(para) >= 20:
            high_quality_paragraphs.append(para)

    # 3. 智能选择最重要的段落
    # 简单策略：保留开头、结尾，以及中间包含关键词的段落
    if not high_quality_paragraphs:
        return text[:max_length]

    # 关键词列表
    key_phrases = [
        '融资', '投资', '估值', '上市', 'IPO', '并购', '收购',
        '技术', '研发', '创新', '突破', '发布', '推出',
        '数据', '增长', '下降', '营收', '利润', '用户',
        '认为', '指出', '强调', '透露', '宣布', '表示',
        '将', '计划', '预计', '可能', '未来', '目前', '现在'
    ]

    scored_paragraphs = []
    for i, para in enumerate(high_quality_paragraphs):
        score = 0

        # 开头和结尾段落加分
        if i < 3:
            score += 3
        elif i >= len(high_quality_paragraphs) - 3:
            score += 3

        # 包含关键词加分
        para_lower = para.lower()
        for phrase in key_phrases:
            if phrase in para_lower:
                score += 2

        # 段落长度适中加分
        if 50 <= len(para) <= 300:
            score += 1

        scored_paragraphs.append((score, i, para))

    # 按分数排序，选择高分段落
    scored_paragraphs.sort(reverse=True)
    selected_paragraphs = sorted(
        [para for score, i, para in scored_paragraphs[:20]],  # 选择前 20 个高分段落
        key=lambda p: high_quality_paragraphs.index(p)
    )

    # 4. 组合并限制长度
    compressed_text = '\n\n'.join(selected_paragraphs)

    if len(compressed_text) > max_length:
        # 如果还是太长，截断但保留完整性
        compressed_text = compressed_text[:max_length]
        last_period = compressed_text.rfind('。')
        if last_period > max_length * 0.8:  # 如果最后一个句号在合理位置
            compressed_text = compressed_text[:last_period + 1]

    return compressed_text.strip()


def detect_long_content(text: str, threshold: int = 10000) -> bool:
    """
    检测是否为长内容

    Args:
        text: 文本内容
        threshold: 长度阈值

    Returns:
        是否为长内容
    """
    return len(text) > threshold


def adaptive_preprocess(text: str) -> Tuple[str, bool]:
    """
    自适应预处理：根据内容长度决定是否压缩

    Args:
        text: 原始文本

    Returns:
        (处理后的文本, 是否进行了压缩)
    """
    if detect_long_content(text):
        compressed = preprocess_wechat_article(text)
        return compressed, len(compressed) < len(text)
    return text, False


if __name__ == "__main__":
    # 测试用例
    test_text = """
    这是一篇很长的文章...
    """ * 100

    processed, was_compressed = adaptive_preprocess(test_text)
    print(f"Original length: {len(test_text)}")
    print(f"Processed length: {len(processed)}")
    print(f"Was compressed: {was_compressed}")
    print(f"Compression ratio: {len(processed) / len(test_text):.2%}")