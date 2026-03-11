import argparse
import json
import re
import unicodedata
from pathlib import Path

import pandas as pd


def normalize_name(name):
    if name is None:
        return ""
    name = unicodedata.normalize("NFKC", str(name))
    name = name.strip()
    name = re.sub(r"[\s\u3000]+", "", name)
    name = re.sub(r"[・･\.\,·'’`´\-‐-‒–—―ー]", "", name)
    return name.lower()


def read_csv_auto(file_path):
    encodings = ["utf-8", "utf-8-sig", "cp932", "shift_jis"]
    last_error = None
    for enc in encodings:
        try:
            df = pd.read_csv(file_path, encoding=enc)
            print(f"CSVを {enc} で読み込みました")
            return df
        except Exception as e:
            last_error = e
    raise ValueError(f"CSVの文字コードを判定できませんでした: {last_error}")


def get_name(rec):
    paths = [
        ("name",),
        ("researcher_name",),
        ("meta", "name"),
        ("meta", "researcher_name"),
        ("researcher", "name"),
        ("profile", "name"),
    ]
    for path in paths:
        cur = rec
        ok = True
        for p in path:
            if isinstance(cur, dict) and p in cur:
                cur = cur[p]
            else:
                ok = False
                break
        if ok and cur:
            return str(cur)
    return None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-jsonl", required=True)
    parser.add_argument("--csv", required=True)
    parser.add_argument("--output-jsonl", required=True)
    args = parser.parse_args()

    jsonl_file = Path(args.input_jsonl)
    csv_file = Path(args.csv)
    output_file = Path(args.output_jsonl)

    df = read_csv_auto(csv_file)

    NAME_COL = "指導教員"
    TITLE_COL = "修士論文主題"

    name_to_titles = {}
    for _, row in df.iterrows():
        name = row[NAME_COL]
        title = row[TITLE_COL]
        if pd.isna(name) or pd.isna(title):
            continue
        key = normalize_name(name)
        title = str(title).strip()
        name_to_titles.setdefault(key, [])
        if title not in name_to_titles[key]:
            name_to_titles[key].append(title)

    records = []
    with open(jsonl_file, "r", encoding="utf-8") as f:
        for line in f:
            if line.strip():
                records.append(json.loads(line))

    updated_count = 0
    for rec in records:
        name = get_name(rec)
        if not name:
            continue

        key = normalize_name(name)
        titles = name_to_titles.get(key)
        if not titles:
            continue

        rec.setdefault("meta", {})
        existing = rec["meta"].get("masters_thesis_titles", [])

        if existing is None:
            existing = []
        elif not isinstance(existing, list):
            existing = [existing]

        merged = [str(x).strip() for x in existing if str(x).strip()]
        for t in titles:
            if t not in merged:
                merged.append(t)

        rec["meta"]["masters_thesis_titles"] = merged
        updated_count += 1

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"完了: {output_file}")
    print(f"更新レコード数: {updated_count}")


if __name__ == "__main__":
    main()