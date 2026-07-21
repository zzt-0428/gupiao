"""
Flask 后端服务 — 多源新闻聚合 + AI 分析 API

启动方式：
    python server.py
    python server.py --port 8765
    python server.py --debug

API 端点：
    GET  /api/health                 — 健康检查
    GET  /api/news/<code>?name=xxx   — 获取个股新闻
    GET  /api/ai/analyze/<code>?name=xxx&mode=full  — AI 分析
    GET  /api/sources                — 数据源状态
"""
import argparse
import json
import os
import sys
import time
from datetime import datetime

import flask
from flask import Flask, request, jsonify
from flask_cors import CORS

from news_fetcher import fetch_all_news
from ai_analyzer import analyze_news, quick_summary

# ===== 配置 =====

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(BASE_DIR, 'config.json')

app = Flask(__name__)
CORS(app)  # 允许所有来源的跨域请求（本地使用）


def load_config() -> dict:
    """加载配置"""
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


# ===== 简单内存缓存 =====

class SimpleCache:
    """TTL 内存缓存"""

    def __init__(self, ttl_seconds: int = 300):
        self._cache = {}
        self._ttl = ttl_seconds

    def get(self, key: str):
        entry = self._cache.get(key)
        if not entry:
            return None
        if time.time() > entry['expires']:
            del self._cache[key]
            return None
        return entry['data']

    def set(self, key: str, data):
        self._cache[key] = {
            'data': data,
            'expires': time.time() + self._ttl,
        }

    def clear(self):
        self._cache = {}


news_cache = SimpleCache(ttl_seconds=180)   # 新闻缓存 3 分钟
ai_cache = SimpleCache(ttl_seconds=600)     # AI 分析缓存 10 分钟


# ===== 辅助函数 =====

def get_time_str():
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


# ===== API 路由 =====

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查端点"""
    config = load_config()
    has_api_key = bool(config.get('deepseekApiKey', ''))

    return jsonify({
        'status': 'ok',
        'server': 'Stock Portfolio News Service',
        'version': '1.0.0',
        'time': get_time_str(),
        'sources': ['eastmoney', 'cninfo', 'sina', 'baidu'],
        'aiAvailable': has_api_key,
    })


@app.route('/api/news/<code>', methods=['GET'])
def get_news(code: str):
    """
    获取个股多源新闻聚合

    URL 参数:
        name  — 股票名称（可选，用于搜索准确性）
        limit — 返回条数上限（默认 30）
        force — 强制刷新缓存（任意值即刷新）

    示例:
        GET /api/news/600900?name=长江电力
        GET /api/news/600900?name=长江电力&limit=10&force=1
    """
    name = request.args.get('name', '')
    limit = request.args.get('limit', 30, type=int)
    force = request.args.get('force') is not None

    # 股票代码基本校验
    if not code or len(code) != 6 or not code.isdigit():
        return jsonify({
            'success': False,
            'error': f'无效的股票代码：{code}（需要6位数字）',
        }), 400

    # 缓存检查
    cache_key = f'news:{code}'
    if not force:
        cached = news_cache.get(cache_key)
        if cached:
            cached['cached'] = True
            # 限制返回条数
            if len(cached.get('news', [])) > limit:
                cached['news'] = cached['news'][:limit]
                cached['count'] = limit
            return jsonify(cached)

    # 获取新闻
    try:
        result = fetch_all_news(code, name)
        result['cached'] = False

        # 限制返回条数
        if len(result.get('news', [])) > limit:
            result['news'] = result['news'][:limit]
            result['count'] = limit

        # 写入缓存
        news_cache.set(cache_key, result)

        return jsonify(result)
    except Exception as e:
        return jsonify({
            'success': False,
            'error': f'获取新闻失败：{str(e)}',
            'code': code,
            'name': name,
        }), 500


@app.route('/api/ai/analyze/<code>', methods=['GET'])
def ai_analyze(code: str):
    """
    AI 分析个股新闻

    URL 参数:
        name — 股票名称
        mode — 分析模式："full"（完整分析，默认）或 "quick"（快速摘要）

    示例:
        GET /api/ai/analyze/600900?name=长江电力&mode=full
        GET /api/ai/analyze/600900?name=长江电力&mode=quick
    """
    name = request.args.get('name', '')
    mode = request.args.get('mode', 'full')

    if not code or len(code) != 6 or not code.isdigit():
        return jsonify({
            'success': False,
            'error': f'无效的股票代码：{code}',
        }), 400

    # 先获取新闻（总会获取最新，除非缓存命中）
    news_result = fetch_all_news(code, name)
    news_items = news_result.get('news', [])

    if not news_items:
        return jsonify({
            'success': False,
            'error': f'未找到 {name or code} 的相关新闻，无法进行 AI 分析',
            'code': code,
            'name': name,
        }), 404

    # AI 分析缓存键
    cache_key = f'ai:{code}:{mode}'
    cached = ai_cache.get(cache_key)
    if cached:
        return jsonify(cached)

    # 调用 AI 分析
    if mode == 'quick':
        analysis_result = quick_summary(code, name, news_items)
    else:
        analysis_result = analyze_news(code, name, news_items)

    # 组装响应
    response = {
        'code': code,
        'name': name or news_result.get('name', ''),
        'newsCount': len(news_items),
        'mode': mode,
        'updatedAt': get_time_str(),
        **analysis_result,
    }

    # 写入缓存
    if analysis_result.get('success'):
        ai_cache.set(cache_key, response)

    return jsonify(response)


@app.route('/api/sources', methods=['GET'])
def get_sources():
    """获取数据源状态"""
    return jsonify({
        'sources': [
            {
                'id': 'eastmoney',
                'name': '东方财富',
                'icon': '📊',
                'description': '个股新闻、公告、研报',
                'category': 'financial_news',
            },
            {
                'id': 'cninfo',
                'name': '巨潮资讯',
                'icon': '📜',
                'description': '证监会指定披露平台，最权威的公告来源',
                'category': 'official_disclosure',
            },
            {
                'id': 'sina',
                'name': '新浪财经',
                'icon': '🌐',
                'description': '宏观政策、行业新闻、市场动态',
                'category': 'market_news',
            },
            {
                'id': 'baidu',
                'name': '百度新闻',
                'icon': '🔍',
                'description': '全网搜索，覆盖非财经类政策新闻',
                'category': 'web_search',
            },
        ],
        'aiProvider': {
            'name': 'DeepSeek',
            'model': 'deepseek-chat',
            'available': bool(load_config().get('deepseekApiKey', '')),
        },
    })


# ===== 错误处理 =====

@app.errorhandler(404)
def not_found(e):
    return jsonify({
        'success': False,
        'error': '端点不存在',
        'hint': '可用端点：/api/health, /api/news/<code>, /api/ai/analyze/<code>, /api/sources',
    }), 404


@app.errorhandler(500)
def server_error(e):
    return jsonify({
        'success': False,
        'error': '服务器内部错误',
    }), 500


# ===== 启动 =====

def main():
    # Windows 下设置控制台编码为 UTF-8 以支持中文和 emoji
    if sys.platform == 'win32':
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(description='股票持仓管家·资讯服务')
    parser.add_argument('--port', '-p', type=int, default=8765, help='服务端口（默认 8765）')
    parser.add_argument('--debug', '-d', action='store_true', help='调试模式')
    parser.add_argument('--host', default='127.0.0.1', help='绑定地址（默认 127.0.0.1）')

    args = parser.parse_args()

    print('=' * 55)
    print('  股票持仓管家 · 多源资讯服务')
    print('=' * 55)
    print(f'  地址: http://{args.host}:{args.port}')
    print(f'  健康检查: http://{args.host}:{args.port}/api/health')
    print(f'  新闻接口: http://{args.host}:{args.port}/api/news/<code>')
    print(f'  AI分析: http://{args.host}:{args.port}/api/ai/analyze/<code>')
    print(f'  数据源: http://{args.host}:{args.port}/api/sources')
    print('-' * 55)
    print(f'  数据源: 东方财富 | 巨潮资讯 | 新浪财经 | 百度新闻')
    print(f'  AI引擎: DeepSeek (deepseek-chat)')
    print(f'  调试模式: {"开启" if args.debug else "关闭"}')
    print('=' * 55)
    print()

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug,
        threaded=True,  # 多线程处理并发请求
    )


if __name__ == '__main__':
    main()
