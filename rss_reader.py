#!/usr/bin/env python3
"""
简易 Reddit RSS 阅读器
从 Reddit 自定义 feed 的 RSS 获取帖子并提取所有链接
"""

import re
import json
import argparse
from datetime import datetime
from pathlib import Path
from html import unescape

import feedparser
import requests


def fetch_rss(rss_url: str, proxy: str = None) -> feedparser.FeedParserDict:
    """获取 RSS feed"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }

    proxies = {"http": proxy, "https": proxy} if proxy else None

    response = requests.get(rss_url, headers=headers, proxies=proxies, timeout=30)
    response.raise_for_status()

    return feedparser.parse(response.content)


def extract_links_from_html(html_content: str) -> list[str]:
    """从 HTML 内容中提取所有链接"""
    # 匹配 href 属性中的链接
    href_pattern = r'href=["\']([^"\']+)["\']'
    # 匹配 src 属性中的链接
    src_pattern = r'src=["\']([^"\']+)["\']'
    # 匹配纯文本中的 URL
    url_pattern = r'https?://[^\s<>"\')\]]+'

    links = set()

    # 提取 href 链接
    for match in re.findall(href_pattern, html_content):
        links.add(unescape(match))

    # 提取 src 链接
    for match in re.findall(src_pattern, html_content):
        links.add(unescape(match))

    # 提取纯 URL
    for match in re.findall(url_pattern, html_content):
        links.add(unescape(match))

    return sorted(links)


def parse_feed(feed: feedparser.FeedParserDict) -> list[dict]:
    """解析 RSS feed 并提取帖子信息"""
    posts = []

    for entry in feed.entries:
        post = {
            "id": entry.get("id", ""),
            "title": entry.get("title", ""),
            "author": entry.get("author", ""),
            "link": entry.get("link", ""),  # Reddit 帖子链接
            "published": entry.get("published", ""),
            "updated": entry.get("updated", ""),
            "content_links": [],  # 帖子内容中的链接
        }

        # 提取内容中的链接
        content = ""
        if hasattr(entry, "content") and entry.content:
            content = entry.content[0].get("value", "")
        elif hasattr(entry, "summary"):
            content = entry.summary or ""

        post["content_links"] = extract_links_from_html(content)

        # 保存原始内容摘要
        post["summary"] = entry.get("summary", "")[:500] if entry.get("summary") else ""

        posts.append(post)

    return posts


def save_results(posts: list[dict], output_file: str):
    """保存结果到 JSONL 文件"""
    output_path = Path(output_file)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for post in posts:
            f.write(json.dumps(post, ensure_ascii=False) + "\n")

    print(f"结果已保存到: {output_file}")


def print_summary(posts: list[dict]):
    """打印摘要信息"""
    print(f"\n{'='*60}")
    print(f"共获取 {len(posts)} 个帖子")
    print(f"{'='*60}\n")

    all_links = set()

    for i, post in enumerate(posts, 1):
        print(f"[{i}] {post['title']}")
        print(f"    作者: {post['author']}")
        print(f"    帖子链接: {post['link']}")

        if post["content_links"]:
            print(f"    内容链接 ({len(post['content_links'])} 个):")
            for link in post["content_links"]:
                print(f"      - {link}")
                all_links.add(link)
        print()

    # 汇总所有链接
    print(f"\n{'='*60}")
    print(f"所有唯一链接汇总 ({len(all_links)} 个)")
    print(f"{'='*60}")
    for link in sorted(all_links):
        print(link)


def main():
    parser = argparse.ArgumentParser(description="Reddit RSS 阅读器 - 提取帖子链接")
    parser.add_argument(
        "--rss-url",
        default="https://www.reddit.com/user/bushacker/m/myreddit/new.rss",
        help="RSS feed URL"
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="代理服务器 (例如: http://127.0.0.1:7890)"
    )
    parser.add_argument(
        "--output",
        default="./data/rss_posts.jsonl",
        help="输出文件路径"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出到终端"
    )

    args = parser.parse_args()

    print(f"正在获取 RSS: {args.rss_url}")

    try:
        feed = fetch_rss(args.rss_url, args.proxy if args.proxy else None)

        if feed.bozo and feed.bozo_exception:
            print(f"警告: RSS 解析有问题 - {feed.bozo_exception}")

        posts = parse_feed(feed)

        if args.json:
            print(json.dumps(posts, ensure_ascii=False, indent=2))
        else:
            print_summary(posts)

        save_results(posts, args.output)

    except requests.RequestException as e:
        print(f"网络请求失败: {e}")
        return 1
    except Exception as e:
        print(f"发生错误: {e}")
        return 1

    return 0


if __name__ == "__main__":
    exit(main())
