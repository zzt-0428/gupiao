"""
AI 分析模块 — 使用 DeepSeek 对聚合新闻进行智能分析
"""
import json
import os
import time
import requests

# ===== 配置 =====

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')


def load_config() -> dict:
    """加载 config.json"""
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def get_api_key() -> str:
    """获取 DeepSeek API Key"""
    config = load_config()
    return config.get('deepseekApiKey', '')


# ===== Prompt 模板 =====

NEWS_ANALYSIS_PROMPT = """你是资深A股投资研究员，兼具政策解读和实战交易经验。

以下是关于 **{name}**（股票代码 {code}）的最新全网资讯汇总，共 {count} 条：

{news_text}

请严格按以下五个模块输出分析报告：

## 一、核心新闻摘要
从上述资讯中提炼最重要的3-5条，每条约50字概括，按重要性排序。

## 二、政策与行业影响分析
- 是否有政策利好/利空？影响程度如何（重大/中等/轻微）？
- 行业趋势是向上还是向下？关键驱动因素是什么？
- 与同行业其他公司相比，该股的相对位置如何？

## 三、短期操作建议（1-4周）
基于消息面和技术面判断短期走势：
- 建议：观望 / 逢低加仓 / 持有 / 减仓
- 给出具体理由（不要套话，要有逻辑链）

## 四、长期配置研判（3-12个月）
- 这些新闻是否影响该股的长期投资逻辑？
- 长线持有的核心逻辑是否依旧成立？
- 是否需要调整目标仓位？

## 五、关键风险提示
- 标注需要重点关注的负面信号
- 如：大股东减持、监管问询、业绩变脸、行业政策转向等
- 每一条风险给出触发条件和应对预案

要求：简洁务实，拒绝套话空话，每条结论都要有依据。用中文输出。"""


def build_news_text(news_items: list, max_items: int = 20) -> str:
    """将新闻列表格式化为提示词文本"""
    lines = []
    for i, item in enumerate(news_items[:max_items], 1):
        source = item.get('source', '未知')
        time_str = item.get('time', '')
        title = item.get('title', '')
        summary = item.get('summary', '')
        news_type = item.get('type', 'news')

        type_label = {'news': '新闻', 'announcement': '公告', 'policy': '政策', 'report': '研报'}.get(news_type, '资讯')

        lines.append(f"{i}. [{source}·{type_label}] {time_str}")
        lines.append(f"   标题：{title}")
        if summary and summary != title:
            lines.append(f"   摘要：{summary}")
        lines.append("")

    return '\n'.join(lines)


def analyze_news(code: str, name: str, news_items: list) -> dict:
    """
    调用 DeepSeek 对新闻进行智能分析

    Args:
        code: 股票代码
        name: 股票名称
        news_items: 新闻列表

    Returns:
        {
            "success": True,
            "analysis": "Markdown 格式的分析报告",
            "tokensUsed": 2847,
            "model": "deepseek-chat"
        }
    """
    api_key = get_api_key()
    if not api_key:
        return {
            'success': False,
            'error': '未配置 DeepSeek API Key，请在 config.json 中设置 deepseekApiKey',
            'analysis': '',
        }

    if not news_items:
        return {
            'success': False,
            'error': '没有可分析的新闻数据',
            'analysis': '',
        }

    # 构建提示词
    news_text = build_news_text(news_items)
    prompt = NEWS_ANALYSIS_PROMPT.format(
        name=name,
        code=code,
        count=len(news_items),
        news_text=news_text,
    )

    # 调用 DeepSeek API
    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
                'max_tokens': 4096,
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            'success': True,
            'analysis': data['choices'][0]['message']['content'],
            'tokensUsed': data.get('usage', {}).get('total_tokens', 0),
            'model': data.get('model', 'deepseek-chat'),
        }

    except requests.Timeout:
        return {
            'success': False,
            'error': 'DeepSeek API 超时（120秒），新闻数据量可能过大，请稍后重试',
            'analysis': '',
        }
    except requests.RequestException as e:
        return {
            'success': False,
            'error': f'DeepSeek API 调用失败：{str(e)}',
            'analysis': '',
        }
    except (KeyError, IndexError) as e:
        return {
            'success': False,
            'error': f'DeepSeek 返回数据格式异常：{str(e)}',
            'analysis': '',
        }


def quick_summary(code: str, name: str, news_items: list) -> dict:
    """
    快速摘要模式 — 用更短的 prompt 快速获取要点
    用于首次加载时快速展示，用户可以点"深度分析"获取完整报告
    """
    api_key = get_api_key()
    if not api_key:
        return {'success': False, 'error': '未配置 API Key'}

    if not news_items:
        return {'success': False, 'error': '没有新闻数据'}

    # 只取最新 10 条
    top_news = news_items[:10]
    news_lines = [f"- [{n.get('source','')}] {n.get('title','')}" for n in top_news]
    news_text = '\n'.join(news_lines)

    prompt = f"""简要分析 {name}({code}) 的最新资讯，控制在300字以内：

{news_text}

请用3-5个要点概述：1)最重要的消息 2)政策/行业影响 3)操作建议。简洁直白。"""

    try:
        resp = requests.post(
            'https://api.deepseek.com/v1/chat/completions',
            headers={
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {api_key}',
            },
            json={
                'model': 'deepseek-chat',
                'messages': [{'role': 'user', 'content': prompt}],
                'temperature': 0.3,
                'max_tokens': 1024,
            },
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()

        return {
            'success': True,
            'analysis': data['choices'][0]['message']['content'],
            'tokensUsed': data.get('usage', {}).get('total_tokens', 0),
        }

    except Exception as e:
        return {'success': False, 'error': str(e)}


# ===== 测试 =====
if __name__ == '__main__':
    from news_fetcher import fetch_all_news

    code = '600900'
    name = '长江电力'

    print(f'🔍 获取 {name}({code}) 的新闻...')
    result = fetch_all_news(code, name)
    print(f'📊 共 {result["count"]} 条新闻\n')

    print('🤖 调用 DeepSeek 分析...')
    analysis = analyze_news(code, name, result['news'])
    if analysis['success']:
        print(f'✅ 分析完成（{analysis.get("tokensUsed", "?")} tokens）\n')
        print(analysis['analysis'])
    else:
        print(f'❌ 分析失败：{analysis["error"]}')
