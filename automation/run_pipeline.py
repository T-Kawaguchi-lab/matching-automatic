import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]

INCOMING_XLSX = ROOT / "incoming" / "forms_latest.xlsx"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STRUCT_DIR = ROOT / "structured_generation"
TRIOS_DIR = ROOT / "trios_enrich"
APP_DIR = ROOT / "matching_app"

STRUCT_SCRIPT = STRUCT_DIR / "build_structured_cards.py"
TRIOS_SCRIPT = TRIOS_DIR / "trios_enrich_jsonl.py"

URL_SCRIPT = ROOT / "url_builder" / "optional_url_builder.py"
THESIS_SCRIPT = ROOT / "thesis_enrich" / "add_masters_thesis_titles.py"

STRUCT_OUT_JSONL = ROOT / "tmp_structured.jsonl"
STRUCT_OUT_XLSX = ROOT / "tmp_structured.xlsx"
TRIOS_OUT_JSONL = ROOT / "tmp_trios_enriched.jsonl"
THESIS_OUT_JSONL = ROOT / "tmp_thesis_enriched.jsonl"

FINAL_JSONL = DATA_DIR / "researcher_latest.jsonl"
FINAL_URL_CSV = DATA_DIR / "url_latest.csv"
FINAL_SURVEY_HTML_DIR = ROOT / "data" / "survey_html"
STATUS_JSON = DATA_DIR / "pipeline_status.json"
ROLE_OVERRIDE_JSON = DATA_DIR / "role_overrides.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def log(msg):
    print(msg, flush=True)


def run(cmd, cwd=None):
    log(f"[RUN] {' '.join(map(str, cmd))}")
    result = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="ignore",
    )
    print(result.stdout, flush=True)
    if result.returncode != 0:
        print(result.stderr, flush=True)
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))}")


def write_status(status: dict):
    STATUS_JSON.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def ensure_input():
    if not INCOMING_XLSX.exists():
        raise FileNotFoundError(
            f"入力ファイルが見つかりません: {INCOMING_XLSX}\n"
            "Power Automate から forms_latest.xlsx を配置してください。"
        )


def get_nested(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def normalize_identity_text(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def normalize_role_value(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace(" ", "_").replace("-", "_")

    if s in {
        "ai_researcher", "ai", "provider",
        "system_researcher", "system", "ai_research",
        "ai-researcher", "ai_researchers",
        "ai研究者", "ai研究", "ai系", "ai分野",
    }:
        return "ai_researcher"

    if s in {
        "other_field_researcher", "other", "needs",
        "science_researcher", "domain_researcher",
        "non_ai", "other_field",
        "other-field-researcher", "domain",
        "他分野研究者", "非ai", "非_ai", "non-ai",
    }:
        return "other_field_researcher"

    return s


def build_person_key(r: Dict[str, Any]) -> str:
    meta = r.get("meta", {}) if isinstance(r.get("meta", {}), dict) else {}

    email = normalize_identity_text(meta.get("email"))
    if email:
        return f"email:{email}"

    matched_url = normalize_identity_text(get_nested(r, "trios.matched_url"))
    if matched_url:
        return f"trios:{matched_url.rstrip('/')}"

    name = normalize_identity_text(meta.get("name") or meta.get("name_raw"))
    affiliation = normalize_identity_text(meta.get("affiliation"))
    position = normalize_identity_text(meta.get("position"))
    research_field = normalize_identity_text(meta.get("research_field"))

    return f"fallback:{name}|{affiliation}|{position}|{research_field}"


def load_jsonl(path: Path):
    rows = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def load_existing_role_overrides() -> Dict[str, str]:
    candidates = [
        ROLE_OVERRIDE_JSON,
        APP_DIR / "data" / "role_overrides.json",
    ]
    for cand in candidates:
        if not cand.exists():
            continue
        try:
            data = json.loads(cand.read_text(encoding="utf-8"))
        except Exception:
            continue

        if not isinstance(data, dict):
            continue

        out: Dict[str, str] = {}
        for k, v in data.items():
            nk = str(k).strip()
            nv = normalize_role_value(v)
            if nk and nv:
                out[nk] = nv
        return out

    return {}


def generate_role_overrides(status):
    rows = load_jsonl(FINAL_JSONL)
    existing = load_existing_role_overrides()
    generated: Dict[str, str] = {}

    for r in rows:
        person_key = build_person_key(r)

        role_raw = get_nested(r, "meta.role")
        if role_raw is None:
            role_raw = get_nested(r, "role")

        # 既存overrideがあれば優先
        role_norm = normalize_role_value(existing.get(person_key, role_raw))

        if person_key and role_norm:
            generated[person_key] = role_norm

    ROLE_OVERRIDE_JSON.write_text(
        json.dumps(dict(sorted(generated.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    status["role_overrides"] = {
        "status": "ok",
        "output_json": str(ROLE_OVERRIDE_JSON),
        "count": len(generated),
        "time": now_iso(),
    }


def run_structured_generation(status):
    if not STRUCT_SCRIPT.exists():
        raise FileNotFoundError(f"構造化生成スクリプトがありません: {STRUCT_SCRIPT}")

    run(
        [
            sys.executable,
            str(STRUCT_SCRIPT),
            "--input", str(INCOMING_XLSX),
            "--out-jsonl", str(STRUCT_OUT_JSONL),
            "--out-xlsx", str(STRUCT_OUT_XLSX),
        ],
        cwd=STRUCT_DIR,
    )

    status["structured_generation"] = {
        "status": "ok",
        "output_jsonl": str(STRUCT_OUT_JSONL),
        "output_xlsx": str(STRUCT_OUT_XLSX),
        "time": now_iso(),
    }


def run_optional_url_generation(status):
    if not URL_SCRIPT.exists():
        raise FileNotFoundError(f"URL生成スクリプトがありません: {URL_SCRIPT}")

    streamlit_base_url = "https://matching-automatic-e7d9kjg9bivjjndmqvuw93.streamlit.app/~/+"

    run(
        [
            sys.executable,
            str(URL_SCRIPT),
            "--input-xlsx", str(INCOMING_XLSX),
            "--output-csv", str(FINAL_URL_CSV),
            "--output-html-dir", str(FINAL_SURVEY_HTML_DIR),
            "--streamlit-base-url", streamlit_base_url,
        ],
        cwd=ROOT / "url_builder",
    )

    status["url_generation"] = {
        "status": "ok",
        "mode": "local_streamlit_preview",
        "output_csv": str(FINAL_URL_CSV),
        "output_html_dir": str(FINAL_SURVEY_HTML_DIR),
        "streamlit_base_url": streamlit_base_url,
        "time": now_iso(),
    }


def run_trios(status):
    if not TRIOS_SCRIPT.exists():
        raise FileNotFoundError(f"TRIOS追加スクリプトがありません: {TRIOS_SCRIPT}")

    run(
        [
            sys.executable,
            str(TRIOS_SCRIPT),
            "--in", str(STRUCT_OUT_JSONL),
            "--out", str(TRIOS_OUT_JSONL),
            "--online",
        ],
        cwd=TRIOS_DIR,
    )

    status["trios_enrich"] = {
        "status": "ok",
        "output_jsonl": str(TRIOS_OUT_JSONL),
        "time": now_iso(),
    }


def run_optional_thesis(status):
    if THESIS_SCRIPT.exists():
        run(
            [
                sys.executable,
                str(THESIS_SCRIPT),
                "--input-jsonl", str(TRIOS_OUT_JSONL),
                "--csv", str(ROOT / "thesis_enrich" / "masters_thesis.csv"),
                "--output-jsonl", str(THESIS_OUT_JSONL),
            ],
            cwd=ROOT / "thesis_enrich",
        )
        shutil.copy2(THESIS_OUT_JSONL, FINAL_JSONL)
        status["thesis_enrich"] = {
            "status": "ok",
            "mode": "real_script",
            "output_jsonl": str(FINAL_JSONL),
            "time": now_iso(),
        }
    else:
        shutil.copy2(TRIOS_OUT_JSONL, FINAL_JSONL)
        status["thesis_enrich"] = {
            "status": "skipped",
            "mode": "placeholder",
            "reason": "add_masters_thesis_titles.py not found",
            "output_jsonl": str(FINAL_JSONL),
            "time": now_iso(),
        }
        log("[SKIP] 修論追加スクリプトが未配置のため、TRIOS出力をそのまま最終JSONLにしました。")


def copy_to_app_data(status):
    app_data = APP_DIR / "data"
    app_data.mkdir(parents=True, exist_ok=True)

    shutil.copy2(FINAL_JSONL, app_data / FINAL_JSONL.name)
    shutil.copy2(FINAL_URL_CSV, app_data / FINAL_URL_CSV.name)
    shutil.copy2(STATUS_JSON, app_data / STATUS_JSON.name)

    if ROLE_OVERRIDE_JSON.exists():
        shutil.copy2(ROLE_OVERRIDE_JSON, app_data / ROLE_OVERRIDE_JSON.name)

    app_survey_dir = app_data / "survey_html"
    if app_survey_dir.exists():
        shutil.rmtree(app_survey_dir)
    if FINAL_SURVEY_HTML_DIR.exists():
        shutil.copytree(FINAL_SURVEY_HTML_DIR, app_survey_dir)

    status["app_data_sync"] = {
        "status": "ok",
        "copied": [
            str(app_data / FINAL_JSONL.name),
            str(app_data / FINAL_URL_CSV.name),
            str(app_data / STATUS_JSON.name),
            str(app_data / ROLE_OVERRIDE_JSON.name),
            str(app_survey_dir),
        ],
        "time": now_iso(),
    }


def main():
    status = {
        "started_at": now_iso(),
        "input_xlsx": str(INCOMING_XLSX),
    }

    try:
        ensure_input()
        run_structured_generation(status)
        run_optional_url_generation(status)
        run_trios(status)
        run_optional_thesis(status)

        # ここで自動生成
        generate_role_overrides(status)
        write_status(status)

        # app側dataへコピー
        copy_to_app_data(status)

        status["finished_at"] = now_iso()
        status["final_status"] = "ok"
        write_status(status)
        log("[OK] pipeline completed.")

    except Exception as e:
        status["finished_at"] = now_iso()
        status["final_status"] = "error"
        status["error"] = str(e)
        write_status(status)
        log(f"[ERROR] {e}")
        raise


if __name__ == "__main__":
    main()