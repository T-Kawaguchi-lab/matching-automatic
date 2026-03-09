# -*- coding: utf-8 -*-
"""
TRIOS 一括抽出（JSONL -> JSONL）

入力JSONL（1行1レコード）から研究者名を取り出し、
TRIOSで検索→研究者ページを取得→研究課題/論文タイトルを抽出して、
元レコードに `trios` フィールドを追加して出力JSONLに保存します。

【このv3は「ページソース保存」(手動) と「自動取得」(ネット) を組み合わせた設計】
- まずは自動でURL取得（--online）
- 失敗した場合や、最初から手動でやりたい場合は
  ブラウザで「ページのソースを表示」→保存したHTMLを html_cache/ に置いて
  --offline で解析できます。

使い方:
  # 1) 自動（ネットで取得）:
  python trios_enrich_jsonl.py --in input.jsonl --out output.jsonl --online

  # 2) オフライン（保存済みHTMLから）:
  python trios_enrich_jsonl.py --in input.jsonl --out output.jsonl --offline

  # 3) 自動+キャッシュ保存（おすすめ）:
  python trios_enrich_jsonl.py --in input.jsonl --out output.jsonl --online --save-html

入力側の名前の場所:
- obj["meta"]["name"] があればそれを使用
- なければ obj["name"]

HTMLキャッシュの保存先:
- html_cache/<sanitized_name>.html
  （sanitized_name はファイル名に安全な形に自動変換）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
from typing import Dict, List, Optional

import requests
from bs4 import BeautifulSoup

from trios_extract import extract_topics_and_papers_from_html


DEFAULT_BASE = "https://trios.tsukuba.ac.jp"


def get_name(obj: dict) -> Optional[str]:
    # v1_1 JSONL は meta.name にあるのでまずそこを見る
    if isinstance(obj.get("meta"), dict) and isinstance(obj["meta"].get("name"), str):
        name = obj["meta"]["name"].strip()
        return name if name else None
    if isinstance(obj.get("name"), str):
        name = obj["name"].strip()
        return name if name else None
    return None


def sanitize_filename(name: str) -> str:
    # ファイル名に使えない文字を "_" に
    s = name.strip()
    s = re.sub(r"\s+", "", s)  # 空白除去
    s = re.sub(r'[\\/:*?"<>|]', "_", s)
    return s or "unknown"


def fetch(url: str, timeout: int = 30) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; trios-enricher/1.0; +https://example.invalid)"
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    r.encoding = r.apparent_encoding or r.encoding
    return r.text


def search_candidates(base_url: str, name: str, per: int = 100) -> List[Dict[str, str]]:
    q = urllib.parse.quote(name)
    url = f"{base_url}/ja/researchers?q={q}&per={per}"
    html = fetch(url)
    soup = BeautifulSoup(html, "lxml")

    candidates: List[Dict[str, str]] = []

    # 検索結果はリンクが複数混ざる可能性があるので、「研究者詳細っぽい」ものを広めに拾う
    for a in soup.select('a[href*="/researcher/"], a[href*="/researchers/"]'):
        href = a.get("href")
        if not href:
            continue
        text = a.get_text(" ", strip=True)
        if not text:
            continue
        # 数字IDを含むリンクだけ
        if re.search(r"/\d{6,}", href):
            full = urllib.parse.urljoin(base_url, href)
            candidates.append({"display_name": text, "url": full})

    # 重複除去（url）
    seen = set()
    uniq = []
    for c in candidates:
        if c["url"] in seen:
            continue
        seen.add(c["url"])
        uniq.append(c)
    return uniq


def norm(s: str) -> str:
    return re.sub(r"\s+", "", s or "").strip()


def choose_best(name: str, candidates: List[Dict[str, str]]) -> Optional[Dict[str, str]]:
    if not candidates:
        return None
    target = norm(name)

    # 完全一致 / 前方一致
    for c in candidates:
        dn = norm(c.get("display_name", ""))
        if dn == target or dn.startswith(target):
            return c

    return candidates[0]


def load_cached_html(cache_dir: str, name: str) -> Optional[str]:
    fn = os.path.join(cache_dir, sanitize_filename(name) + ".html")
    if os.path.exists(fn):
        with open(fn, "r", encoding="utf-8") as f:
            return f.read()
    return None


def save_cached_html(cache_dir: str, name: str, html: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    fn = os.path.join(cache_dir, sanitize_filename(name) + ".html")
    with open(fn, "w", encoding="utf-8") as f:
        f.write(html)
    return fn


def enrich_one(
    base_url: str,
    name: str,
    cache_dir: str,
    online: bool,
    offline: bool,
    save_html: bool,
    delay: float,
) -> Dict:
    # offline ならまずキャッシュから読む
    if offline:
        cached = load_cached_html(cache_dir, name)
        if not cached:
            return {"status": "offline_no_html", "cache_expected": os.path.join(cache_dir, sanitize_filename(name) + ".html")}
        topics, papers = extract_topics_and_papers_from_html(cached)
        return {"status": "ok_offline", "matched_url": None, "research_topics": topics, "papers": papers}

    # online
    if not online:
        return {"status": "skipped", "reason": "neither --online nor --offline specified"}

    # まず検索
    try:
        candidates = search_candidates(base_url, name)
    except Exception as e:
        return {"status": "search_error", "error": str(e)}

    if not candidates:
        return {"status": "not_found", "candidates": []}

    best = choose_best(name, candidates)
    if not best:
        return {"status": "not_found", "candidates": candidates[:10]}

    matched_url = best["url"]

    if delay:
        time.sleep(delay)

    # 詳細取得
    try:
        html = fetch(matched_url)
        if save_html:
            saved_path = save_cached_html(cache_dir, name, html)
        else:
            saved_path = None

        topics, papers = extract_topics_and_papers_from_html(html)

        out = {
            "status": "ok",
            "matched_url": matched_url,
            "matched_display_name": best.get("display_name", ""),
            "research_topics": topics,
            "papers": papers,
        }
        if saved_path:
            out["saved_html"] = saved_path
        return out

    except Exception as e:
        # 失敗したら、キャッシュがあればそれで救済
        cached = load_cached_html(cache_dir, name)
        if cached:
            try:
                topics, papers = extract_topics_and_papers_from_html(cached)
                return {
                    "status": "ok_offline_fallback",
                    "matched_url": matched_url,
                    "error": str(e),
                    "research_topics": topics,
                    "papers": papers,
                    "cache_used": os.path.join(cache_dir, sanitize_filename(name) + ".html"),
                }
            except Exception as e2:
                return {"status": "detail_error", "matched_url": matched_url, "error": str(e), "offline_fallback_error": str(e2)}
        return {"status": "detail_error", "matched_url": matched_url, "error": str(e)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="inp", required=True, help="入力JSONL")
    ap.add_argument("--out", dest="out", required=True, help="出力JSONL（triosを追記）")
    ap.add_argument("--base-url", default=DEFAULT_BASE, help="TRIOSベースURL（通常変更不要）")
    ap.add_argument("--cache-dir", default="html_cache", help="保存HTMLのディレクトリ")
    ap.add_argument("--online", action="store_true", help="ネットでTRIOSにアクセスして取得する")
    ap.add_argument("--offline", action="store_true", help="html_cacheの保存済みHTMLだけで抽出する")
    ap.add_argument("--save-html", action="store_true", help="取得した研究者ページHTMLをhtml_cacheに保存する（おすすめ）")
    ap.add_argument("--delay", type=float, default=0.2, help="アクセス間隔（秒）")
    ap.add_argument("--limit", type=int, default=0, help="先頭から処理する件数（0=全部）")
    args = ap.parse_args()

    if args.online and args.offline:
        raise SystemExit("ERROR: --online と --offline は同時に指定できません。どちらか一方にしてください。")

    n = 0
    with open(args.inp, "r", encoding="utf-8") as fin, open(args.out, "w", encoding="utf-8") as fout:
        for line in fin:
            line = line.strip()
            if not line:
                continue
            obj = json.loads(line)

            name = get_name(obj)
            if not name:
                obj["trios"] = {"status": "no_name"}
                fout.write(json.dumps(obj, ensure_ascii=False) + "\n")
                continue

            n += 1
            if args.limit and n > args.limit:
                break

            trios_info = enrich_one(
                base_url=args.base_url,
                name=name,
                cache_dir=args.cache_dir,
                online=args.online,
                offline=args.offline,
                save_html=args.save_html,
                delay=args.delay,
            )
            obj["trios"] = trios_info
            fout.write(json.dumps(obj, ensure_ascii=False) + "\n")

            print(f"[{n}] {name} -> {trios_info.get('status')}", file=sys.stderr)

    print(f"Done. Wrote: {args.out}")


if __name__ == "__main__":
    main()
