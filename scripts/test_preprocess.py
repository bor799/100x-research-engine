#!/usr/bin/env python3
"""
测试长文章预处理功能
"""

import sys
sys.path.insert(0, 'src')

from knowledge_extractor_v3.pipeline import _preprocess_long_content

def test_preprocess():
    # 创建一个测试长文本
    test_text = """
# 这是标题

这是一篇非常长的文章，包含了很多内容。我们需要测试预处理功能是否能够正确地压缩这篇文章。

## 第一部分：重要内容

这家公司最近完成了一轮重要的融资，投资方包括知名的风险投资机构。融资金额达到数亿美元，估值大幅提升。

公司创始人表示，这次融资将用于技术研发和市场扩张。他们计划在未来的季度内推出更多创新产品。

数据显示，该公司的用户增长率达到了行业领先水平。营收数据也显示出强劲的增长势头。

## 第二部分：技术细节

技术团队采用了先进的机器学习算法，大幅提升了产品的性能和准确性。这些技术创新是公司竞争优势的重要来源。

研发投入占比持续增加，体现了公司对技术创新的重视。

## 第三部分：市场分析

行业分析师认为，该公司所处的市场具有巨大的增长潜力。市场规模预计将在未来几年内达到数千亿美元。

竞争格局方面，该公司凭借技术优势和市场策略，已经建立了较强的市场地位。

## 第四部分：未来展望

管理层透露，公司正在积极探索新的业务机会。可能包括国际市场扩张和新产品线的开发。

预计将在下一季度发布重要的战略更新。

## 第五部分：风险因素

当然，也存在一些风险需要关注。市场竞争加剧、技术变化快速等因素都可能影响公司的发展。

点击关注我们的公众号，获取更多精彩内容。
转载请注明出处，本文版权所有。
""" * 20  # 重复20次，模拟长文章

    print(f"原始文本长度: {len(test_text)}")
    print(f"原始文本预览: {test_text[:200]}...")

    # 测试预处理
    processed_text = _preprocess_long_content(test_text)
    print(f"\n处理后文本长度: {len(processed_text)}")
    print(f"压缩比例: {len(processed_text) / len(test_text):.2%}")
    print(f"节省字符数: {len(test_text) - len(processed_text)}")

    print(f"\n处理后文本预览:")
    print(processed_text[:500])
    print("...")

    # 验证关键信息是否保留
    key_phrases = ["融资", "投资", "估值", "技术", "增长", "市场"]
    preserved = sum(1 for phrase in key_phrases if phrase in processed_text)
    print(f"\n关键信息保留情况: {preserved}/{len(key_phrases)} 个关键词被保留")

    # 验证噪音是否被移除
    noise_phrases = ["点击关注", "转载注明", "版权所有", "更多精彩"]
    noise_removed = sum(1 for phrase in noise_phrases if phrase not in processed_text)
    print(f"噪音移除情况: {noise_removed}/{len(noise_phrases)} 个噪音短语被移除")

    if len(processed_text) < len(test_text) and preserved >= len(key_phrases) * 0.5:
        print("\n✅ 预处理功能正常工作！")
        return True
    else:
        print("\n❌ 预处理功能可能存在问题")
        return False

if __name__ == "__main__":
    success = test_preprocess()
    sys.exit(0 if success else 1)