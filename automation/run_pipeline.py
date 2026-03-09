import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INCOMING_XLSX = ROOT / "incoming" / "forms_latest.xlsx"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

STRUCT_DIR = ROOT / "structured_generation"
TRIOS_DIR = ROOT / "trios_enrich"
APP_DIR = ROOT / "matching_app"

STRUCT_SCRIPT = STRUCT_DIR / "build_structured_cards.py"
TRIOS_SCRIPT = TRIOS_DIR / "trios_enrich_jsonl.py"

# 今は無いので、将来ここに置く前提
URL_SCRIPT = ROOT / "automation" / "url_builder_real.py"
THESIS_SCRIPT = ROOT / "automation" / "thesis_enricher_real.py"

STRUCT_OUT_JSONL = ROOT / "tmp_structured.jsonl"
STRUCT_OUT_XLSX = ROOT / "tmp_structured.xlsx"
TRIOS_OUT_JSONL = ROOT / "tmp_trios_enriched.jsonl"
THESIS_OUT_JSONL = ROOT / "tmp_thesis_enriched.jsonl"

FINAL_JSONL = DATA_DIR / "researcher_latest.jsonl"
FINAL_URL_CSV = DATA_DIR / "url_latest.csv"
STATUS_JSON = DATA_DIR / "pipeline_status.json"


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
        errors="ignore"
    )
    print(result.stdout, flush=True)
    if result.returncode != 0:
        print(result.stderr, flush=True)
        raise RuntimeError(f"Command failed: {' '.join(map(str, cmd))}")


def write_status(status: dict):
    STATUS_JSON.write_text(
        json.dumps(status, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def ensure_input():
    if not INCOMING_XLSX.exists():
        raise FileNotFoundError(
            f"入力ファイルが見つかりません: {INCOMING_XLSX}\n"
            "Power Automate から forms_latest.xlsx を配置してください。"
        )


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
        cwd=STRUCT_DIR
    )
    status["structured_generation"] = {
        "status": "ok",
        "output_jsonl": str(STRUCT_OUT_JSONL),
        "output_xlsx": str(STRUCT_OUT_XLSX),
        "time": now_iso(),
    }


def run_optional_url_generation(status):
    if URL_SCRIPT.exists():
        run(
            [
                sys.executable,
                str(URL_SCRIPT),
                "--input-xlsx", str(INCOMING_XLSX),
                "--output-csv", str(FINAL_URL_CSV),
            ],
            cwd=ROOT / "automation"
        )
        status["url_generation"] = {
            "status": "ok",
            "mode": "real_script",
            "output_csv": str(FINAL_URL_CSV),
            "time": now_iso(),
        }
    else:
        # 仮ファイルを作る
        FINAL_URL_CSV.write_text(
            "name,url\nDUMMY_USER,https://example.com/form\n",
            encoding="utf-8"
        )

        status["url_generation"] = {
            "status": "skipped",
            "mode": "placeholder",
            "reason": "URL script not found",
            "output_csv": str(FINAL_URL_CSV),
            "time": now_iso(),
        }

        log("[SKIP] URL生成スクリプトが無いため仮の url_latest.csv を作りました")

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
        cwd=TRIOS_DIR
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
                "--output-jsonl", str(THESIS_OUT_JSONL),
            ],
            cwd=ROOT / "automation"
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
            "reason": "thesis_enricher_real.py not found",
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

    status["app_data_sync"] = {
        "status": "ok",
        "copied": [
            str(app_data / FINAL_JSONL.name),
            str(app_data / FINAL_URL_CSV.name),
            str(app_data / STATUS_JSON.name),
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
        write_status(status)   # 一旦ここで保存
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