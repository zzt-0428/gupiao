"""
多源新闻抓取器 - 聚合百度新闻、新浪财经、Bing搜索、东方财富
所有源并行抓取，错误隔离，单个源失败不影响其他源
"""
import json
import re
import time
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

# ===== 工具函数 =====

def get_secid(code: str) -> str:
    """股票代码转东方财富 secid"""
    code = code.strip()
    if not code or len(code) != 6:
        return None
    if code[0] in '659':
        return f'1.{code}'
    if code[0] in '032':
        return f'0.{code}'
    return None


def get_market_prefix(code: str) -> str:
    """获取市场前缀 SH/SZ"""
    if code[0] in '659':
        return 'SH'
    return 'SZ'


def normalize_title(title: str) -> str:
    """标准化标题用于去重"""
    return re.sub(r'\s+', '', str(title).strip())


def title_hash(title: str) -> str:
    """标题哈希用于去重"""
    return hashlib.md5(normalize_title(title).encode()).hexdigest()


def safe_get(url: str, headers: dict = None, params: dict = None,
             timeout: int = 10, retries: int = 2, encoding: str = None) -> requests.Response | None:
    """带重试的 HTTP GET"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/json,application/xhtml+xml,*/*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    if headers:
        default_headers.update(headers)

    for attempt in range(retries):
        try:
            resp = requests.get(url, headers=default_headers, params=params,
                               timeout=timeout, allow_redirects=True)
            if encoding:
                resp.encoding = encoding
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(0.5)
    return None


def safe_post(url: str, headers: dict = None, data: dict = None,
              timeout: int = 10, retries: int = 2) -> requests.Response | None:
    """带重试的 HTTP POST"""
    default_headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36',
        'Accept': 'application/json, text/html, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    if headers:
        default_headers.update(headers)

    for attempt in range(retries):
        try:
            resp = requests.post(url, headers=default_headers, data=data,
                                timeout=timeout, allow_redirects=True)
            resp.raise_for_status()
            return resp
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(0.5)
    return None


# ===== 数据源：百度新闻搜索 =====

def fetch_baidu_news(code: str, name: str = '') -> list:
    """
    百度新闻搜索 - 全网政策与新闻
    搜索策略：先搜 "{name} {code} 公告 政策"，再搜 "{name} 最新消息"
    """
    results = []
    queries = []

    if name:
        queries.append(f'{name} {code} 公告 政策')
        queries.append(f'{name} {code} 最新消息')
    else:
        queries.append(f'{code} 公告 新闻')

    for query in queries:
        try:
            url = 'https://www.baidu.com/s'
            params = {'tn': 'news', 'word': query, 'pn': 0}
            resp = safe_get(url, params=params, timeout=10)
            if not resp:
                continue

            soup = BeautifulSoup(resp.text, 'lxml')

            for item in soup.select('.result-op, .result, .news-item, article, .c-container'):
                title_el = item.select_one('h3 a, .c-title a, .news-title a, .c-title')
                if not title_el:
                    continue

                title = title_el.get_text(strip=True)
                href = title_el.get('href', '') or title_el.get('data-url', '')
                if not href or len(title) < 4:
                    continue

                # 摘要
                summary_el = item.select_one('.c-summary, .news-summary, .c-abstract, .c-span-last')
                summary = summary_el.get_text(strip=True) if summary_el else ''

                # 时间和来源
                info_el = item.select_one('.c-info, .news-info, .c-color-gray2')
                info_text = info_el.get_text(strip=True) if info_el else ''
                # 提取时间：匹配 YYYY-MM-DD 或 "X小时前" 等
                time_match = re.search(r'(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d+[小时天分钟前]+)', info_text)
                pub_time = time_match.group(1) if time_match else datetime.now().strftime('%Y-%m-%d')

                # 提取来源
                source_match = re.search(r'([一-龥]{2,8}(?:网|报|社|财经|新闻|资讯|快讯))', info_text)
                source_name = source_match.group(1) if source_match else '百度新闻'

                news_type = 'policy' if any(kw in title for kw in ['政策', '监管', '法规', '通知', '公告']) else 'news'

                results.append({
                    'title': title,
                    'summary': summary or title,
                    'url': href,
                    'source': '百度新闻',
                    'sourceIcon': '🔍',
                    'time': pub_time,
                    'type': news_type,
                })

        except Exception:
            continue

    return results


# ===== 数据源：新浪财经 =====

def fetch_sina_finance(code: str, name: str = '') -> list:
    """
    新浪财经滚动新闻 - 按关键词过滤
    """
    results = []
    keywords = [code]
    if name:
        keywords.append(name)

    try:
        url = 'https://feed.mix.sina.com.cn/api/roll/get'
        params = {'pageid': 153, 'lid': 2512, 'num': 30, 'versionNumber': '1.2.8'}
        headers = {
            'Referer': 'https://finance.sina.com.cn/',
            'Accept': 'application/json',
        }
        resp = safe_get(url, headers=headers, params=params, timeout=10)
        if not resp:
            return []

        data = resp.json()
        news_list = data.get('result', {}).get('data', []) or []

        for item in news_list:
            title = item.get('title', '') or item.get('wapTitle', '')
            if not title:
                continue

            # 关键词过滤（只保留包含股票代码或名称的新闻）
            if not any(kw in title for kw in keywords):
                continue

            ctime = item.get('ctime', '')
            if ctime and ctime.isdigit():
                pub_time = datetime.fromtimestamp(int(ctime)).strftime('%Y-%m-%d %H:%M')
            else:
                pub_time = datetime.now().strftime('%Y-%m-%d')

            results.append({
                'title': title,
                'summary': item.get('intro', '') or title,
                'url': item.get('url', ''),
                'source': '新浪财经',
                'sourceIcon': '🌐',
                'time': pub_time,
                'type': 'news',
            })
    except Exception:
        pass

    return results


# ===== 数据源：东方财富（备用） =====

def fetch_eastmoney_search(code: str, name: str = '') -> list:
    """
    东方财富 - 通过搜索接口获取个股新闻
    使用东方财富搜索 API
    """
    results = []
    query = f'{code} {name}' if name else code

    try:
        # 东方财富搜索
        url = 'https://searchapi.eastmoney.com/bussiness/Web/GetCMSSearchResult'
        params = {
            'type': '8196',  # 新闻类型
            'pageindex': 1,
            'pagesize': 15,
            'keyword': query,
            'name': 'zixun',
        }
        headers = {
            'Referer': 'https://so.eastmoney.com/',
            'Accept': 'application/json',
        }
        resp = safe_get(url, headers=headers, params=params, timeout=10)
        if not resp:
            return []

        data = resp.json()
        if data.get('IsSuccess'):
            items = data.get('Data', []) or []
            for item in items:
                pub_time = item.get('date', '') or item.get('showTime', '')
                results.append({
                    'title': item.get('title', '') or item.get('Title', ''),
                    'summary': item.get('content', '') or item.get('Content', '') or '',
                    'url': item.get('url', '') or item.get('Url', ''),
                    'source': '东方财富',
                    'sourceIcon': '📊',
                    'time': pub_time or datetime.now().strftime('%Y-%m-%d'),
                    'type': 'news',
                })
    except Exception:
        pass

    return results


# ===== 数据源：Bing 新闻搜索 =====

def fetch_bing_news(code: str, name: str = '') -> list:
    """
    Bing 新闻搜索 - 作为百度补充
    """
    results = []
    query = f'{name} {code} 股票 新闻' if name else f'{code} 股票 新闻'

    try:
        url = 'https://www.bing.com/news/search'
        params = {'q': query, 'qft': 'interval=7', 'form': 'YFNR'}
        headers = {
            'Accept': 'text/html,application/xhtml+xml,*/*',
            'Accept-Language': 'zh-CN,zh;q=0.9',
        }
        resp = safe_get(url, headers=headers, params=params, timeout=15)
        if not resp:
            return []

        soup = BeautifulSoup(resp.text, 'lxml')

        for card in soup.select('.news-card, .card-withurl, article'):
            title_el = card.select_one('a.title, .news-title, h2 a, h3 a')
            if not title_el:
                continue

            title = title_el.get_text(strip=True)
            href = title_el.get('href', '')
            if not href or len(title) < 4:
                continue

            snippet_el = card.select_one('.snippet, .news-snippet, .news-snpt, p')
            summary = snippet_el.get_text(strip=True) if snippet_el else ''

            source_el = card.select_one('.source, .news-source')
            source_name = source_el.get_text(strip=True) if source_el else 'Bing新闻'

            time_el = card.select_one('.news-dt, time, .source span:last-child')
            pub_time = time_el.get_text(strip=True) if time_el else datetime.now().strftime('%Y-%m-%d')

            results.append({
                'title': title,
                'summary': summary or title,
                'url': href,
                'source': 'Bing新闻',
                'sourceIcon': '🔎',
                'time': pub_time,
                'type': 'news',
            })
    except Exception:
        pass

    return results


# ===== 聚合入口 =====

def fetch_all_news(code: str, name: str = '') -> dict:
    """
    并行从多个来源获取新闻，去重合并返回

    Returns:
        {
            "code": "600900",
            "name": "长江电力",
            "news": [{title, summary, url, source, sourceIcon, time, type}, ...],
            "count": N,
            "sources": {"百度新闻": 15, "新浪财经": 5, ...},
            "updatedAt": "2026-07-20 14:30:00"
        }
    """
    fetchers = [
        ('百度新闻', lambda: fetch_baidu_news(code, name)),
        ('新浪财经', lambda: fetch_sina_finance(code, name)),
        ('东方财富', lambda: fetch_eastmoney_search(code, name)),
        ('Bing新闻', lambda: fetch_bing_news(code, name)),
    ]

    all_items = []
    sources_count = {}

    # 并行抓取
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = {executor.submit(fn): src_name for src_name, fn in fetchers}
        for future in as_completed(futures):
            src_name = futures[future]
            try:
                items = future.result(timeout=20)
                all_items.extend(items)
                sources_count[src_name] = len(items)
            except Exception:
                sources_count[src_name] = 0

    # 去重（基于标题哈希）
    seen = set()
    deduped = []
    for item in all_items:
        h = title_hash(item['title'])
        if h not in seen:
            seen.add(h)
            deduped.append(item)

    # 按时间倒序排列
    deduped.sort(key=lambda x: str(x.get('time', '')), reverse=True)

    # 限制最多 50 条
    deduped = deduped[:50]

    # 如果没有新闻，尝试更广泛的搜索
    if len(deduped) == 0 and name:
        # 只用股票名搜一次百度
        try:
            fallback = fetch_baidu_news(code, '') or []
            deduped = fallback[:20]
            sources_count['百度新闻(扩展)'] = len(deduped)
        except Exception:
            pass

    return {
        'code': code,
        'name': name,
        'news': deduped,
        'count': len(deduped),
        'sources': {k: v for k, v in sources_count.items() if v > 0},
        'updatedAt': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


# ===== 命令行测试 =====
if __name__ == '__main__':
    import sys
    code = sys.argv[1] if len(sys.argv) > 1 else '600900'
    name = sys.argv[2] if len(sys.argv) > 2 else '长江电力'

    print(f'Searching news for {name}({code})...')
    result = fetch_all_news(code, name)

    print(f'\nTotal: {result["count"]} news items from {result["sources"]}')
    print(f'Updated: {result["updatedAt"]}\n')

    for i, item in enumerate(result['news'][:15], 1):
        icon = item.get('sourceIcon', '-')
        tag = item.get('type', 'news')
        print(f'{i:2d}. [{icon} {item["source"]}][{tag}] {item["title"]}')
        if item.get('summary') and item['summary'] != item['title']:
            print(f'    {item["summary"][:100]}')
        print(f'    {item.get("time", "?")} | {item["url"][:80]}')
        print()
