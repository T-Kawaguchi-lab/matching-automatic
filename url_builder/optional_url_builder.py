import argparse
import html
import re
import shutil
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]

D = 3
H = 7
I = 8
AM = 38
V = 21

AI_TEXT = "AIそのものやAIの高度化を研究している/I conduct research on AI itself or on the advancement of AI technologies."


def esc(x):
    return html.escape("" if pd.isna(x) else str(x))


def safe_id(x):
    return re.sub(r"[^\w\-]", "", str(x))


def is_empty(x):
    if pd.isna(x):
        return True
    if isinstance(x, str) and x.strip() == "":
        return True
    return False


def norm(x):
    if pd.isna(x):
        return ""
    s = str(x)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    s = re.sub(r"[ \t\u3000]+", " ", s)
    s = re.sub(r"\n+", "\n", s).strip()
    return s


def role_from_v(x):
    return "AI_researcher" if norm(x) == norm(AI_TEXT) else "Domain_researcher"


def reset_output_dir(path: Path):
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def build_streamlit_preview_url(base_url: str, rid: str) -> str:
    rid_safe = safe_id(rid)
    if not base_url or not str(base_url).strip():
        return f"?preview_id={rid_safe}"
    return f"{str(base_url).rstrip('/')}/?preview_id={rid_safe}"



def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-xlsx", required=True)
    parser.add_argument("--output-csv", required=True)
    parser.add_argument("--output-html-dir", required=True)
    parser.add_argument("--streamlit-base-url", default="")
    args = parser.parse_args()

    input_xlsx = Path(args.input_xlsx)
    output_csv = Path(args.output_csv)
    output_html_dir = Path(args.output_html_dir)
    streamlit_base_url = args.streamlit_base_url.strip()

    print(f"使用するExcel: {input_xlsx}")
    df = pd.read_excel(input_xlsx)

    reset_output_dir(output_html_dir)
    output_html_dir.mkdir(parents=True, exist_ok=True)

    ids = [f"R{i:04d}" for i in range(1, len(df) + 1)]

    for rid, (_, row) in zip(ids, df.iterrows()):
        rid_safe = safe_id(rid)
        page_dir = output_html_dir / rid_safe
        page_dir.mkdir(parents=True, exist_ok=True)

        rows = []

        for idx in [H, D]:
            col = df.columns[idx]
            val = row.iloc[idx]
            rows.append(f"<tr><th>{esc(col)}</th><td>{esc(val)}</td></tr>")

        for idx in range(I, AM + 1):
            val = row.iloc[idx]
            if is_empty(val):
                continue
            col = df.columns[idx]
            rows.append(f"<tr><th>{esc(col)}</th><td>{esc(val)}</td></tr>")

        rows_html = "\n".join(rows)
        if rows_html.strip() == "":
            rows_html = '<tr><th>Message</th><td>表示できる回答がありません</td></tr>'

        html_doc = f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex,nofollow">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{rid_safe}</title>
<style>
:root{{--bg:#f6f8fc;--card:#ffffff;--line:#d8e0ef;--text:#1f2937;--muted:#5b6472;--accent:#1d4ed8;}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;line-height:1.65}}
.wrapper{{max-width:1100px;margin:0 auto;padding:24px 16px 40px}}
.hero{{background:linear-gradient(135deg,#eff6ff,#ffffff);border:1px solid var(--line);border-radius:18px;padding:22px 20px;margin-bottom:18px;box-shadow:0 10px 30px rgba(15,23,42,.06)}}
.hero h1{{margin:0 0 6px;font-size:26px}}
.hero p{{margin:0;color:var(--muted)}}
.meta{{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:18px 0 22px}}
.item,.card{{background:var(--card);border:1px solid var(--line);border-radius:16px;box-shadow:0 6px 20px rgba(15,23,42,.04)}}
.item{{padding:14px 16px}}
.card{{padding:18px 18px;margin-bottom:14px}}
.label{{font-size:13px;font-weight:700;color:var(--accent);margin-bottom:8px}}
.value{{white-space:pre-wrap;word-break:break-word;font-size:15px}}
.section-title{{font-size:18px;font-weight:700;margin:8px 0 12px}}
</style>
</head>
<body>
<div class="wrapper">
  <div class="hero">
    <h1>アンケート表示 / Survey Viewer</h1>
    <p>ID: {rid_safe}</p>
  </div>
  <div class="meta">
    {meta_html}
  </div>
  <div class="section-title">回答内容 / Responses</div>
  {body_html}
</div>
</body>
</html>
"""
        (page_dir / "index.html").write_text(html_doc, encoding="utf-8")

    url_df = pd.DataFrame({
        "id": ids,
        "name": df.iloc[:, H].astype(str),
        "email": df.iloc[:, D].astype(str),
        "affiliation": df.iloc[:, I].astype(str),
        "role": df.iloc[:, V].apply(role_from_v),
    })

    url_df["streamlit_preview_url"] = url_df["id"].apply(
        lambda x: build_streamlit_preview_url(streamlit_base_url, x)
    )
    url_df["html_rel_path"] = url_df["id"].apply(lambda x: f"survey_html/{safe_id(x)}/index.html")
    url_df["url"] = url_df["streamlit_preview_url"]

    output_csv.parent.mkdir(parents=True, exist_ok=True)
    url_df.to_csv(output_csv, index=False, encoding="utf-8-sig")

    print("✅ HTML生成完了")
    print(f" - CSV: {output_csv}")
    print(f" - HTML dir: {output_html_dir}")
    print(f" - 例: {output_html_dir / 'R0001' / 'index.html'}")


if __name__ == "__main__":
    main()