# -*- coding: utf-8 -*-
"""
TRIOS (筑波大学研究者総覧) ページHTMLから
- 研究課題: <dt>研究課題</dt> の直後 <dd> 内 table の各行の「1列目」だけ
- 論文: <dt>論文</dt> の直後 <dd> 内 <li> の最初の <b> のテキストだけ
を抽出して JSON で出力します。

使い方:
  python trios_extract.py --html page.html
  python trios_extract.py --url "https://trios.tsukuba.ac.jp/ja/researchers/0000004129"

出力:
  {
    "research_topics": [...],
    "papers": [...]
  }
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import List, Tuple, Optional

import requests
from bs4 import BeautifulSoup


def _find_dd_by_dt(soup: BeautifulSoup, dt_text: str):
    dt = soup.find("dt", string=lambda s: s and s.strip() == dt_text)
    if not dt:
        return None
    return dt.find_next_sibling("dd")


def extract_topics_and_papers_from_html(html: str) -> Tuple[List[str], List[str]]:
    soup = BeautifulSoup(html, "lxml")

    # ===== 研究課題 =====
    topics: List[str] = []
    dd_topics = _find_dd_by_dt(soup, "研究課題")
    if dd_topics:
        for tr in dd_topics.select("table tbody tr"):
            tds = tr.find_all("td", recursive=False)
            if not tds:
                continue
            title = tds[0].get_text(" ", strip=True)
            # 「さらに表示...」などを除外
            if title and "さらに表示" not in title:
                topics.append(title)

    # ===== 論文 =====
    papers: List[str] = []
    dd_papers = _find_dd_by_dt(soup, "論文")
    if dd_papers:
        for li in dd_papers.select("ul > li"):
            b = li.find("b")
            if not b:
                continue
            title = b.get_text(" ", strip=True)
            if title and "さらに表示" not in title:
                papers.append(title)

    return topics, papers


def load_html(args) -> str:
    if args.html:
        with open(args.html, "r", encoding="utf-8") as f:
            return f.read()

    if args.url:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; trios-extractor/1.0; +https://example.invalid)"
        }
        r = requests.get(args.url, headers=headers, timeout=30)
        r.raise_for_status()
        # TRIOSは日本語ページだとUTF-8が多いが、念のため requests の推定に従う
        r.encoding = r.apparent_encoding or r.encoding
        return r.text

    # stdin
    data = sys.stdin.read()
    if not data.strip():
        raise SystemExit("ERROR: --html か --url を指定するか、HTMLを標準入力に渡してください。")
    return data


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--html", help="保存したHTMLファイル（ページソース）")
    ap.add_argument("--url", help="TRIOSの研究者ページURL")
    ap.add_argument("--out", default="", help="出力JSONファイル（省略時は標準出力）")
    args = ap.parse_args()

    html = load_html(args)
    topics, papers = extract_topics_and_papers_from_html(html)

    obj = {"research_topics": topics, "papers": papers}

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=2)
        print(f"OK: wrote {args.out} (topics={len(topics)}, papers={len(papers)})")
    else:
        print(json.dumps(obj, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
