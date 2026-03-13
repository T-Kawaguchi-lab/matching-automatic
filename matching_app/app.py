import json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple
import streamlit.components.v1 as components
import numpy as np
import pandas as pd
import streamlit as st
from sentence_transformers import SentenceTransformer

# ------------------------
# Fixed settings
# ------------------------
DEFAULT_MODEL = "intfloat/multilingual-e5-large"
ROLE_PATH = "meta.role"

# 旧データ互換＋新データで使いそうな候補も追加
TEXT_KEY_PRIORITY = [
    "match_text.canonical_card_text",
    "match_text",  # match_text が文字列の場合
    "e5_text",
    "e5_passage",
    "e5_query",
    "card_text",
    "canonical_card_text",
]

DEFAULT_WEIGHT_A = 0.4
DEFAULT_WEIGHT_B = 0.4
DEFAULT_WEIGHT_C = 0.2


st.set_page_config(page_title="AI↔他分野 推薦 version3/ AI↔Domain Matching", layout="wide")
st.title("AI研究者 ↔ 他分野研究者 推薦 / AI↔Domain Researcher Matching")

import base64
import os
from typing import Any, Dict, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request


APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
ROLE_OVERRIDE_PATH = DATA_DIR / "role_overrides.json"


def get_secret(name: str, default: str = "") -> str:
    # Streamlit secrets 優先、なければ環境変数
    try:
        if name in st.secrets:
            return str(st.secrets[name]).strip()
    except Exception:
        pass
    return os.getenv(name, default).strip()


GITHUB_REPO = get_secret("GITHUB_REPO", "")
GITHUB_BRANCH = get_secret("GITHUB_BRANCH", "main") or "main"
GITHUB_TOKEN = get_secret("GITHUB_TOKEN", "")

GITHUB_ROLE_PATHS = [
    "data/role_overrides.json",
    "matching_app/data/role_overrides.json",
]


def normalize_identity_text(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace("　", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def get_nested(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


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


def load_role_overrides() -> Dict[str, str]:
    if not ROLE_OVERRIDE_PATH.exists():
        return {}

    try:
        data = json.loads(ROLE_OVERRIDE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(data, dict):
        return {}

    out: Dict[str, str] = {}
    for k, v in data.items():
        nk = str(k).strip()
        nv = normalize_role_value(v)
        if nk and nv:
            out[nk] = nv
    return out


def save_role_overrides_local(data: Dict[str, str]) -> None:
    ROLE_OVERRIDE_PATH.parent.mkdir(parents=True, exist_ok=True)
    ROLE_OVERRIDE_PATH.write_text(
        json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def github_sync_enabled() -> bool:
    return bool(GITHUB_REPO and GITHUB_TOKEN)


def github_headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {GITHUB_TOKEN}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "streamlit-role-overrides-sync",
    }


def github_get_file_sha(repo_path: str) -> str:
    if not github_sync_enabled():
        return ""

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}?ref={GITHUB_BRANCH}"
    req = urllib_request.Request(url, headers=github_headers(), method="GET")

    try:
        with urllib_request.urlopen(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        return str(payload.get("sha") or "")
    except urllib_error.HTTPError as e:
        if e.code == 404:
            return ""
        raise


def github_put_file(repo_path: str, content_text: str, message: str) -> None:
    body = {
        "message": message,
        "content": base64.b64encode(content_text.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    sha = github_get_file_sha(repo_path)
    if sha:
        body["sha"] = sha

    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{repo_path}"
    req = urllib_request.Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={**github_headers(), "Content-Type": "application/json"},
        method="PUT",
    )

    with urllib_request.urlopen(req, timeout=20):
        pass


def sync_role_overrides_to_github(data: Dict[str, str], actor_name: str, person_key: str) -> Tuple[bool, str]:
    if not github_sync_enabled():
        return False, "GitHub連携設定がないため、このapp内だけ保存しました。"

    content_text = json.dumps(dict(sorted(data.items())), ensure_ascii=False, indent=2) + "\n"
    message = f"Update role override for {actor_name} ({person_key})"

    try:
        for repo_path in GITHUB_ROLE_PATHS:
            github_put_file(repo_path, content_text, message)
        return True, "GitHub に同期しました。他のPCでも反映されます。"
    except Exception as e:
        return False, f"ローカル保存は成功しましたが、GitHub同期に失敗しました: {e}"
def get_preview_id_from_query() -> str:
    try:
        q = st.query_params
        raw = q.get("preview_id", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
    except Exception:
        raw = ""
    return re.sub(r"[^\w\-]", "", str(raw or ""))

def get_selected_id_from_query() -> str:
    try:
        q = st.query_params
        raw = q.get("selected_id", "")
        if isinstance(raw, list):
            raw = raw[0] if raw else ""
    except Exception:
        raw = ""
    return re.sub(r"[^\w\-]", "", str(raw or ""))

def render_preview_page(preview_id: str) -> None:
    st.title("アンケート表示 / Survey Viewer")

    html_text = read_html_preview(preview_id)
    if not html_text:
        st.error(f"HTMLが見つかりませんでした: {preview_id}")
        st.caption("survey_html/<preview_id>/index.html が必要です。 / survey_html/<preview_id>/index.html is required.")
        st.stop()

    selected_id = get_selected_id_from_query()

    back_url = "./"
    if selected_id:
        back_url = f"./?selected_id={selected_id}"

    try:
        st.markdown(
            f'<a href="{back_url}" target="_self">一覧へ戻る / Back to results</a>',
            unsafe_allow_html=True
        )
    except Exception:
        pass

    components.html(html_text, height=900, scrolling=True)
    st.stop()

def read_jsonl_from_path(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def read_jsonl_from_uploaded(uploaded) -> List[Dict[str, Any]]:
    content = uploaded.getvalue().decode("utf-8", errors="ignore").splitlines()
    return [json.loads(line) for line in content if line.strip()]


def read_csv_from_path(path: Path) -> pd.DataFrame:
    return pd.read_csv(path)


def read_csv_from_uploaded(uploaded) -> pd.DataFrame:
    return pd.read_csv(uploaded)

def read_html_preview(preview_id: str) -> str:
    preview_id = re.sub(r"[^\w\-]", "", str(preview_id or ""))
    if not preview_id:
        return ""

    html_path = DATA_DIR / "survey_html" / preview_id / "index.html"
    if not html_path.exists():
        return ""

    return html_path.read_text(encoding="utf-8", errors="ignore")

def get_nested(d: Dict[str, Any], path: str) -> Any:
    cur: Any = d
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def normalize_role_value(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip().lower()
    s = s.replace(" ", "_").replace("-", "_")

    # AI側の表記ゆれ
    if s in {
        "ai_researcher", "ai", "provider",
        "system_researcher", "system", "ai_research",
        "ai-researcher", "ai_researchers",
        "ai研究者", "ai研究", "ai系", "ai分野"
    }:
        return "ai_researcher"

    # 他分野側の表記ゆれ
    if s in {
        "other_field_researcher", "other", "needs",
        "science_researcher", "domain_researcher",
        "non_ai", "other_field",
        "other-field-researcher", "domain",
        "他分野研究者", "非ai", "非_ai", "non-ai"
    }:
        return "other_field_researcher"

    return s


def ensure_prefix(text: str, prefix: str) -> str:
    t = (text or "").strip()
    if not t:
        return ""
    if re.match(r"^\s*(query:|passage:)\s*", t, flags=re.IGNORECASE):
        t = re.sub(r"^\s*(query:|passage:)\s*", prefix + " ", t, flags=re.IGNORECASE)
        return t.strip()
    return f"{prefix} {t}".strip()


def summarize_one_line(r: Dict[str, Any]) -> str:
    v = get_nested(r, "match_text.one_line_pitch")
    if isinstance(v, str) and v.strip():
        return v.strip()

    v2 = r.get("match_text")
    if isinstance(v2, str) and v2.strip():
        s = v2.strip()
        return (s[:160] + "…") if len(s) > 160 else s

    v = get_nested(r, "match_text.canonical_card_text")
    if isinstance(v, str) and v.strip():
        s = v.strip()
        return (s[:160] + "…") if len(s) > 160 else s
    return ""


def _as_list(x):
    if x is None:
        return []
    if isinstance(x, list):
        return [str(v).strip() for v in x if str(v).strip()]
    s = str(x).strip()
    return [s] if s else []


def _join(xs, sep=", "):
    xs = [str(x).strip() for x in xs if str(x).strip()]
    return sep.join(xs)


def strip_outer_parens(s: str) -> str:
    if s is None:
        return ""
    t = str(s).strip()

    pairs = [("(", ")"), ("（", "）")]

    for l, r in pairs:
        if t.startswith(l) and t.endswith(r) and len(t) >= 2:
            t = t[1:-1].strip()
            break

    return t


def _cap_list(xs: List[str], max_items: int = 50) -> List[str]:
    xs2 = [str(x).strip() for x in xs if str(x).strip()]
    return xs2[:max_items]

def normalize_exact_token(s: Any) -> str:
    """
    完全一致判定用の正規化。
    部分一致は禁止なので、文字列全体を正規化して比較するだけ。
    """
    if s is None:
        return ""
    t = str(s).strip().lower()
    t = t.replace("　", " ")
    t = re.sub(r"\s+", " ", t)
    return t

def has_real_content(text: str) -> bool:
    """
    prefix(Task系の説明文)を除いた実データが存在するか判定する
    """
    if text is None:
        return False

    t = str(text).strip()
    if not t:
        return False

    lines = [line.strip() for line in t.splitlines()]

    cleaned = []
    for line in lines:
        if not line:
            continue
        if line.startswith("Task:"):
            continue
        if line.startswith("Match based on research theme similarity"):
            continue
        cleaned.append(line)

    if not cleaned:
        return False

    real_text = "\n".join(cleaned).strip()
    return len(real_text) > 0

def get_a_side_raw_items(r: Dict[str, Any]) -> List[str]:
    """
    A+ 完全一致判定用の元項目を取得する。
    Domain側:
      - needs.task_type_hints
      - needs.need_ai_category_hints / needs.needed_ai_category_hints
    AI側:
      - offers.ai_categories_raw
      - offers.methods_keyword / offers.methods_keywords
    """
    role_raw = (get_nested(r, "meta.role") or get_nested(r, "role") or "").lower()
    is_domain = ("domain" in role_raw) or ("other" in role_raw)

    if is_domain:
        xs1 = _as_list(get_nested(r, "needs.task_type_hints"))
        xs2 = _as_list(
            get_nested(r, "needs.need_ai_category_hints")
            or get_nested(r, "needs.needed_ai_category_hints")
            or get_nested(r, "need_ai_category_hints")
            or get_nested(r, "needed_ai_category_hints")
            or []
        )
        return xs1 + xs2

    xs1 = _as_list(get_nested(r, "offers.ai_categories_raw"))
    xs2 = _as_list(
        get_nested(r, "offers.methods_keyword")
        or get_nested(r, "offers.methods_keywords")
        or []
    )
    return xs1 + xs2


def exact_match_words_between_a(query_items: List[str], doc_items: List[str]) -> List[str]:
    """
    完全一致のみ採用。
    例:
      - classification vs classification -> 一致
      - 画像解析 vs データ解析 -> 不一致
    """
    q_map = {}
    for x in query_items:
        nx = normalize_exact_token(x)
        if nx:
            q_map[nx] = str(x).strip()

    d_map = {}
    for x in doc_items:
        nx = normalize_exact_token(x)
        if nx:
            d_map[nx] = str(x).strip()

    matched_keys = sorted(set(q_map.keys()) & set(d_map.keys()))
    return [q_map[k] for k in matched_keys]


def _esc_html(v):
    import html
    if pd.isna(v):
        return ""
    return html.escape(str(v))

def build_embedding_texts_three_axes(r: Dict[str, Any]) -> Tuple[str, str, str, str]:
    """
    役割ごとに指定項目のみを使い、E5用の入力テキストを3本作る：
      A：AI研究分野
      B：AI研究内容
      C：自身の研究

    さらにデバッグ表示用に全文 debug_text も返す。
    """

    role_raw = (get_nested(r, "meta.role") or get_nested(r, "role") or "").lower()
    is_domain = ("domain" in role_raw) or ("other" in role_raw)
    task_prefix_domain = (
        "Task: Find an AI researcher who can advance this research with AI.\n"
        "Match based on research theme similarity, applicable AI methods, and feasibility.\n"
    )

    task_prefix_ai = (
        "Task: Find a domain research problem this AI researcher can help solve.\n"
        "Match based on research theme similarity, applicable AI methods, and feasibility.\n"
    )
    research_field = (get_nested(r, "meta.research_field") or r.get("research_field") or "").strip()

    trios_topics = _as_list(get_nested(r, "trios.research_topics"))
    trios_papers = _as_list(get_nested(r, "trios.papers"))
    trios_topics = _cap_list(trios_topics, 50)
    trios_papers = _cap_list(trios_papers, 50)
    #（両roleで共通）
    masters_thesis_titles = [
        strip_outer_parens(x)
        for x in (get_nested(r, "meta.masters_thesis_titles") or [])
        if str(x).strip()
    ]

    if is_domain:
        # -------------------------
        # Domain researcher
        # -------------------------
        themes = _as_list(get_nested(r, "project.themes"))
        academic_challenge_overview = (get_nested(r, "project.academic_challenge_overview") or "").strip()
        ai_leverage_and_impact = (get_nested(r, "project.ai_leverage_and_impact") or "").strip()

        sources = (get_nested(r, "data.sources_and_collection") or "").strip()

        # ユーザー要望の date_typees_raw も拾う
        data_types_raw = (
            (get_nested(r, "data.date_typees_raw") or "").strip()
            or (get_nested(r, "data.data_types_raw") or "").strip()
        )

        modalities = _as_list(get_nested(r, "data.modalities"))
        basic_info = (get_nested(r, "data.basic_info") or "").strip()
        complexity_flags = _as_list(get_nested(r, "data.complexity_flags"))
        complexity_raw = _as_list(get_nested(r, "data.complexity_raw"))

        task_type_hints = _as_list(get_nested(r, "needs.task_type_hints"))

        need_ai_hints = (
            get_nested(r, "needs.need_ai_category_hints")
            or get_nested(r, "needs.needed_ai_category_hints")
            or get_nested(r, "need_ai_category_hints")
            or get_nested(r, "needed_ai_category_hints")
            or []
        )
        need_ai_hints = _as_list(need_ai_hints)

        # A：AI研究分野
        lines_a = []
        if task_type_hints:
            lines_a.append(f"Task type hints / 想定タスク: {_join(_cap_list(task_type_hints, 30))}")
        if need_ai_hints:
            lines_a.append(f"Needed AI category / 必要AI領域: {_join(_cap_list(need_ai_hints, 30))}")
        text_a = (task_prefix_domain + "\n".join(lines_a).strip()).strip()

        # B：AI研究内容
        lines_b = []
        if themes:
            lines_b.append(f"Research Themes for AI Application / AI活用研究テーマ: {_join(_cap_list(themes, 20))}")
        if academic_challenge_overview:
            lines_b.append(f"Research Problems to Solve with AI / AI活用における学術課題: {academic_challenge_overview}")
        if ai_leverage_and_impact:
            lines_b.append(f"AI leverage & impact / AI活用の方針・期待インパクト: {ai_leverage_and_impact}")
        if sources:
            lines_b.append(f"Data sources & collection / データ出所・収集方法: {sources}")
        if data_types_raw:
            lines_b.append(f"Data types / データ種別: {data_types_raw}")
        if modalities:
            lines_b.append(f"Modalities / モダリティ: {_join(_cap_list(modalities, 20))}")
        if basic_info:
            lines_b.append(f"Basic info / データ基本情報: {basic_info}")
        if complexity_raw:
            lines_b.append(f"Complexity / 複雑性: {_join(_cap_list(complexity_raw, 20))}")
        elif complexity_flags:
            lines_b.append(f"Complexity / 複雑性: {_join(_cap_list(complexity_flags, 20))}")
        text_b = (task_prefix_domain + "\n".join(lines_b).strip()).strip()

        # C：自身の研究
        lines_c = []
        if research_field:
            lines_c.append(f"Domain Research field / 他分野研究分野: {research_field}")
        if masters_thesis_titles:
            lines_c.append(f"My supervised master’s thesis topics / 担当修論テーマ: {_join(masters_thesis_titles)}")
        if trios_topics:
            lines_c.append(f"Research Topics / 研究トピック: {_join(trios_topics)}")
        if trios_papers:
            lines_c.append(f"Previous Paper Topics / 過去論文テーマ: {_join(trios_papers)}")
        text_c = (task_prefix_domain + "\n".join(lines_c).strip()).strip()

    else:
        # -------------------------
        # AI researcher
        # -------------------------
        ai_categories_raw = _as_list(get_nested(r, "offers.ai_categories_raw"))

        methods_keyword = (
            get_nested(r, "offers.methods_keyword")
            or get_nested(r, "offers.methods_keywords")
            or []
        )
        methods_keyword = _as_list(methods_keyword)

        current_main_research_themes = _as_list(get_nested(r, "offers.current_main_research_themes"))

        # A：AI研究分野
        lines_a = []
        if ai_categories_raw:
            lines_a.append(f"AI categories / AI領域: {_join(_cap_list(ai_categories_raw, 30))}")
        if methods_keyword:
            lines_a.append(f"AI methods keywords / AI手法キーワード: {_join(_cap_list(methods_keyword, 30))}")
        text_a = (task_prefix_ai + "\n".join(lines_a).strip()).strip()

        # B：AI研究内容
        lines_b = []
        if current_main_research_themes:
            lines_b.append(f"Main AI research themes / 主なAI研究テーマ: {_join(_cap_list(current_main_research_themes, 30))}")
        if masters_thesis_titles:
            lines_b.append(f"My supervised master’s thesis topics / 担当修論テーマ: {_join(masters_thesis_titles)}")
        if trios_topics:
            lines_b.append(f"Research Topics / 研究トピック: {_join(trios_topics)}")
        if trios_papers:
            lines_b.append(f"Previous Paper Topics / 過去論文テーマ: {_join(trios_papers)}")
        text_b = (task_prefix_ai + "\n".join(lines_b).strip()).strip()

        # C：自身の研究
        lines_c = []
        if research_field:
            lines_c.append(f"AI Research field / AI研究分野: {research_field}")
        if masters_thesis_titles:
            lines_c.append(f"My supervised master’s thesis topics / 担当修論テーマ: {_join(masters_thesis_titles)}")
        if trios_topics:
            lines_c.append(f"Research Topics / 研究トピック: {_join(trios_topics)}")
        if trios_papers:
            lines_c.append(f"Previous Paper Topics / 過去論文テーマ: {_join(trios_papers)}")
        text_c = (task_prefix_ai + "\n".join(lines_c).strip()).strip()

    debug_sections = []
    if text_a:
        debug_sections.append("[A: AI研究分野 / AI Research Area]\n" + text_a)
    if text_b:
        debug_sections.append("[B: AI研究内容 / AI Research Content]\n" + text_b)
    if text_c:
        debug_sections.append("[C: 自身の研究 / Own Research]\n" + text_c)
    debug_text = "\n\n".join(debug_sections).strip()

    return text_a, text_b, text_c, debug_text


def get_text_by_priority(r: Dict[str, Any], priorities: List[str]) -> str:
    for key in priorities:
        v = get_nested(r, key)
        if isinstance(v, str) and v.strip():
            return v.strip()

    v = get_nested(r, "match_text.canonical_card_text")
    if isinstance(v, str) and v.strip():
        return v.strip()

    v2 = r.get("match_text")
    if isinstance(v2, str) and v2.strip():
        return v2.strip()

    return json.dumps(r.get("meta", {}), ensure_ascii=False)


def build_id(i_1based: int) -> str:
    return f"R{i_1based:04d}"


@st.cache_resource
def load_model(model_name: str) -> SentenceTransformer:
    return SentenceTransformer(model_name)


@st.cache_data(show_spinner=False)
def encode_texts(model_name: str, texts: List[str], mode: str) -> np.ndarray:
    """
    mode: "query" or "passage"
    E5: query側は query:、doc側は passage: を付けて normalize_embeddings=True で埋め込み
    """
    model = load_model(model_name)
    if mode not in {"query", "passage"}:
        raise ValueError("mode must be 'query' or 'passage'")
    pref = "query:" if mode == "query" else "passage:"
    prep = [ensure_prefix(t, pref) for t in texts]
    emb = model.encode(prep, normalize_embeddings=True, show_progress_bar=False)
    return np.asarray(emb, dtype=np.float32)


@st.cache_data(show_spinner=False)
def precompute_similarity_matrices(
    model_name: str,
    ai_texts_a: List[str],
    ai_texts_b: List[str],
    ai_texts_c: List[str],
    other_texts_a: List[str],
    other_texts_b: List[str],
    other_texts_c: List[str],
) -> Dict[str, np.ndarray]:
    """
    3軸の類似度行列を先に作る（重みなし）:
      - A: AI研究分野
      - B: AI研究内容
      - C: 自身の研究

    2方向:
      - AI(query) -> Other(passage):  [n_ai, n_other]
      - Other(query) -> AI(passage):  [n_other, n_ai]

    重み付けはUI側で後から軽く再計算する。
    """

    # A
    ai_q_a = encode_texts(model_name, ai_texts_a, mode="query")
    ai_p_a = encode_texts(model_name, ai_texts_a, mode="passage")
    ot_q_a = encode_texts(model_name, other_texts_a, mode="query")
    ot_p_a = encode_texts(model_name, other_texts_a, mode="passage")

    sim_ai_to_other_a = (ai_q_a @ ot_p_a.T).astype(np.float32)
    sim_other_to_ai_a = (ot_q_a @ ai_p_a.T).astype(np.float32)

    # B
    ai_q_b = encode_texts(model_name, ai_texts_b, mode="query")
    ai_p_b = encode_texts(model_name, ai_texts_b, mode="passage")
    ot_q_b = encode_texts(model_name, other_texts_b, mode="query")
    ot_p_b = encode_texts(model_name, other_texts_b, mode="passage")

    sim_ai_to_other_b = (ai_q_b @ ot_p_b.T).astype(np.float32)
    sim_other_to_ai_b = (ot_q_b @ ai_p_b.T).astype(np.float32)

    # C
    ai_q_c = encode_texts(model_name, ai_texts_c, mode="query")
    ai_p_c = encode_texts(model_name, ai_texts_c, mode="passage")
    ot_q_c = encode_texts(model_name, other_texts_c, mode="query")
    ot_p_c = encode_texts(model_name, other_texts_c, mode="passage")

    sim_ai_to_other_c = (ai_q_c @ ot_p_c.T).astype(np.float32)
    sim_other_to_ai_c = (ot_q_c @ ai_p_c.T).astype(np.float32)

    return {
        "sim_ai_to_other_a": sim_ai_to_other_a,
        "sim_ai_to_other_b": sim_ai_to_other_b,
        "sim_ai_to_other_c": sim_ai_to_other_c,
        "sim_other_to_ai_a": sim_other_to_ai_a,
        "sim_other_to_ai_b": sim_other_to_ai_b,
        "sim_other_to_ai_c": sim_other_to_ai_c,
    }

preview_id = get_preview_id_from_query()
if preview_id:
    render_preview_page(preview_id)

# ------------------------
# Data selection UI
# ------------------------
with st.sidebar:
    st.header("データ選択（任意） / Data selection (optional)")
    st.caption("デフォルトはリポジトリ内の data/ を使用します。必要ならここで差し替えできます。 / Default uses data/ in the repo; you can replace it here if needed.")

    # JSONL
    jsonl_files = sorted([p.name for p in DATA_DIR.glob("*.jsonl")])
    default_jsonl = jsonl_files[0] if jsonl_files else None

    jsonl_mode = st.radio("JSONLの読み込み / Load JSONL", ["既存ファイルを使う / Use existing", "アップロードして差し替える / Upload & replace"], index=0)
    selected_jsonl_name = None
    uploaded_jsonl = None
    if jsonl_mode == "既存ファイルを使う / Use existing":
        if default_jsonl is None:
            st.error("data/ に JSONL がありません。アップロードしてください。 / No JSONL in data/. Please upload.")
        else:
            selected_jsonl_name = st.selectbox("JSONLファイル / JSONL file", jsonl_files, index=0)
    else:
        uploaded_jsonl = st.file_uploader("JSONLをアップロード / Upload JSONL", type=["jsonl"])

    st.divider()

    # CSV
    csv_files = sorted([p.name for p in DATA_DIR.glob("*.csv")])
    default_csv = "url_mapping_mock.csv" if "url_mapping_mock.csv" in csv_files else (csv_files[0] if csv_files else None)

    csv_mode = st.radio("アンケートCSVの読み込み / Load survey CSV", ["既存ファイルを使う / Use existing", "アップロードして差し替える / Upload & replace"], index=0)
    selected_csv_name = None
    uploaded_csv = None
    if csv_mode == "既存ファイルを使う / Use existing":
        if default_csv is None:
            st.warning("data/ に CSV がありません（URL列は空になります）。必要ならアップロードしてください。 / No CSV in data/ (URL column will be empty). Upload if needed.")
        else:
            idx = csv_files.index(default_csv) if default_csv in csv_files else 0
            selected_csv_name = st.selectbox("CSVファイル / CSV file", csv_files, index=idx)
    else:
        uploaded_csv = st.file_uploader("CSVをアップロード（id,url列がある想定） / Upload CSV (expects id,url)", type=["csv"])

    st.divider()
    st.caption(f"使用モデル / Model : {DEFAULT_MODEL}")

import json
from datetime import datetime
from zoneinfo import ZoneInfo
import streamlit as st

status_file = "data/pipeline_status.json"

with open(status_file, "r", encoding="utf-8") as f:
    status = json.load(f)

finished_at = status.get("finished_at")

if finished_at:
    dt_utc = datetime.fromisoformat(finished_at)
    dt_jst = dt_utc.astimezone(ZoneInfo("Asia/Tokyo"))

    st.write(
        f"最終更新 / Last Update: {dt_jst.strftime('%Y-%m-%d %H:%M:%S')} JST"
    )
else:
    st.write("最終更新時刻が見つかりません")
# ------------------------
# Load selected data
# ------------------------
if jsonl_mode == "アップロードして差し替える / Upload & replace":
    if uploaded_jsonl is None:
        st.warning("JSONLが未指定です。サイドバーでアップロードしてください。 / JSONL not selected. Please upload in the sidebar.")
        st.stop()
    rows = read_jsonl_from_uploaded(uploaded_jsonl)
    jsonl_label = f"uploaded:{uploaded_jsonl.name}"
else:
    if selected_jsonl_name is None:
        st.error("JSONLが見つかりません。data/に置くか、アップロードしてください。 / JSONL not found. Put it in data/ or upload.")
        st.stop()
    rows = read_jsonl_from_path(DATA_DIR / selected_jsonl_name)
    jsonl_label = selected_jsonl_name

if not rows:
    st.error("JSONLが空です。 / JSONL is empty.")
    st.stop()

if csv_mode == "アップロードして差し替える / Upload & replace":
    if uploaded_csv is None:
        map_df = pd.DataFrame(columns=["id", "url"])
        csv_label = "(none)"
    else:
        map_df = read_csv_from_uploaded(uploaded_csv)
        csv_label = f"uploaded:{uploaded_csv.name}"
else:
    if selected_csv_name is None:
        map_df = pd.DataFrame(columns=["id", "url"])
        csv_label = "(none)"
    else:
        map_df = read_csv_from_path(DATA_DIR / selected_csv_name)
        csv_label = selected_csv_name

st.markdown(
    '##### アンケートに回答頂いた方の名前を <a href="#person_selectbox"><b>名前入力欄</b></a> に入力すると，下記のデータを使用して計算した研究者間の類似度を表示します．',
    unsafe_allow_html=True
)

st.markdown(
    '##### If you enter the name of a person who responded to the questionnaire in <a href="#person_selectbox"><b>Type name</b></a>, the similarity between researchers calculated using the data below.',
    unsafe_allow_html=True
)


# ------------------------
# Build df
# ------------------------
records = []
roles_raw = []
role_overrides = load_role_overrides()
rows_for_df = []
for i, r in enumerate(rows, start=1):
    rid = f"R{i:04d}"
    meta = r.get("meta", {}) if isinstance(r.get("meta", {}), dict) else {}
    person_key = build_person_key(r)
    # role: 旧(meta.role)→新(role) の順で取得
    role_raw = get_nested(r, "meta.role")
    if role_raw is None:
        role_raw = get_nested(r, "role")

    override_role = role_overrides.get(person_key)
    if override_role:
        role_n = normalize_role_value(override_role)
    else:
        role_n = normalize_role_value(role_raw)

    rows_for_df.append({
        "id": rid,
        "person_key": person_key,
        "role_norm": role_n,
        "name": get_nested(r, "meta.name") or get_nested(r, "meta.name_raw") or "",
        "research_field": get_nested(r, "meta.research_field") or "",
        "raw": r,
    })
    roles_raw.append(role_raw)

    embed_text_a, embed_text_b, embed_text_c, embed_text = build_embedding_texts_three_axes(r)
    a_raw_items = get_a_side_raw_items(r)
    matched_url = (get_nested(r, "trios.matched_url") or "").strip()
    masters_thesis_titles = get_nested(r, "meta.masters_thesis_titles") or []

    masters_thesis_titles = [
        strip_outer_parens(x)
        for x in masters_thesis_titles
        if str(x).strip()
    ]

    records.append({
        "id": rid,
        "person_key": person_key,
        "role_norm": role_n,
        "name": meta.get("name") or meta.get("name_raw") or "",
        "affiliation": meta.get("affiliation") or "",
        "position": meta.get("position") or "",
        "research_field": meta.get("research_field") or "",
        "summary": summarize_one_line(r),
        "embed_text_a": embed_text_a,
        "embed_text_b": embed_text_b,
        "embed_text_c": embed_text_c,
        "embed_text": embed_text,
        "a_raw_items": a_raw_items,
        "has_a": has_real_content(embed_text_a),
        "has_b": has_real_content(embed_text_b),
        "has_c": has_real_content(embed_text_c),
        "matched_url": matched_url,
        "masters_thesis_titles": masters_thesis_titles,
        "role_raw": "" if role_raw is None else str(role_raw),
    })

df = pd.DataFrame(records)

if not map_df.empty and "id" in map_df.columns:
    merge_cols = ["id"]
    for c in ["url", "streamlit_preview_url", "html_rel_path"]:
        if c in map_df.columns:
            merge_cols.append(c)

    df = df.merge(map_df[merge_cols], on="id", how="left")
else:
    df["url"] = ""
    df["streamlit_preview_url"] = ""
    df["html_rel_path"] = ""

if "url" not in df.columns:
    df["url"] = ""
if "streamlit_preview_url" not in df.columns:
    df["streamlit_preview_url"] = ""
if "html_rel_path" not in df.columns:
    df["html_rel_path"] = ""

ai_df = df[df["role_norm"] == "ai_researcher"].reset_index(drop=True)
other_df = df[df["role_norm"] == "other_field_researcher"].reset_index(drop=True)

c1, c2, c3 = st.columns(3)
c1.metric("総件数 / Total", len(df))
c2.metric("AI研究者 / AI", len(ai_df))
c3.metric("他分野研究者 / Domain", len(other_df))

if len(ai_df) == 0 or len(other_df) == 0:
    st.warning("role分離の結果、片側が0件です。meta.role の値（表記ゆれ）を確認してください。 / After role split, one side is 0. Please check meta.role values (variants).")
    st.write("role_rawのユニーク（先頭30）: ", sorted({str(v) for v in roles_raw if v is not None})[:30])
    st.stop()

st.markdown("""
### 研究者区分の定義 / Definition of Researcher Categories

**AI研究者 / AI Researcher：**
            
AI for Science「チャレンジ型」公募に向けたアンケート調査【項目2：研究へのAIの活用経験と意識】の回答が「AIそのものやAIの高度化を研究している」を選択した方

Those who selected“I conduct research on AI itself or on the advancement of AI technologies.” in Item 2 of the AI for Science “Challenge-Type” Call for Proposals survey.

**他分野研究者 / Domain Researcher：**
            
上記以外の選択肢を選んだ方/Those who selected any other response in the same survey item.
""")

# ------------------------
# Precompute (HEAVY) ONCE
# ------------------------

st.markdown("""
### 入力データ一覧 / Input Data List

- アンケート結果  / Questionnaire results  
- TRIOS  
- 下記学位プログラム2025年度修論タイトル及び指導教員リスト  
    - サービス工学学位プログラム / Master’s Program in Service Engineering  
    - 社会工学学位プログラム / Master’s Program in Policy and Planning Sciences  
    - 知能機能システム学位プログラム / Master’s/Doctoral Program in Intelligent and Mechanical Interaction Systems  
    - リスク・レジリエンス工学学位プログラム / Master’s/Doctoral Program in Risk and Resilience Engineering  
    - 情報理工学位プログラム / Master’s/Doctoral Program in Computer Science  
""")

with st.spinner("全員分の類似度を事前計算しています。（初回のみとても重いです）10分程度かかります。... / Precomputing similarity (very heavy only on first run; may take ~10 minutes)..."):
    mats = precompute_similarity_matrices(
        DEFAULT_MODEL,
        ai_df["embed_text_a"].fillna("").astype(str).tolist(),
        ai_df["embed_text_b"].fillna("").astype(str).tolist(),
        ai_df["embed_text_c"].fillna("").astype(str).tolist(),
        other_df["embed_text_a"].fillna("").astype(str).tolist(),
        other_df["embed_text_b"].fillna("").astype(str).tolist(),
        other_df["embed_text_c"].fillna("").astype(str).tolist(),
    )
st.write("### 事前計算 / Precompute")
st.success("事前計算完了 / Precompute finished")

# ---- 初期重み ----
weight_a = DEFAULT_WEIGHT_A
weight_b = DEFAULT_WEIGHT_B
weight_c = DEFAULT_WEIGHT_C
st.write("### 重み変更 / Change Weights")
st.caption("ここで重みを変更すると、事前計算済みの A/B/C 類似度を使って再計算します。 / Changing weights here only recombines precomputed A/B/C similarities.")
st.markdown("""
#### 類似度計算項目の定義 / Definition of Similarity Components

##### A：AI研究分野 / AI Research Area 

- **他分野研究者 / Domain Researcher**  
  - Task type hints / 想定タスク  
  - Needed AI category / 必要AI領域

- **AI研究者 / AI Researcher**  
  - AI categories / AI領域  
  - AI methods keywords / AI手法キーワード

---

##### B：AI研究内容 / AI Research Content  

- **他分野研究者 / Domain Researcher**  
  - Research Themes for AI Application / AI活用研究テーマ  
  - Research Problems to Solve with AI / AI活用における学術課題  
  - AI leverage & impact / AI活用の方針・期待インパクト  
  - Data sources & collection / データ出所・収集方法  
  - Data types / データ種別  
  - Modalities / モダリティ  
  - Basic info / データ基本情報  
  - Complexity / 複雑性  

- **AI研究者 / AI Researcher**  
  - Main AI research themes / 主なAI研究テーマ
  - My supervised master’s thesis topics / 担当修論テーマ  
  - TRIOS Research Topics / TRIOS 研究トピック  
  - TRIOS Previous Paper Topics / TRIOS過去論文テーマ

---

##### C：自身の研究 / Own Research 

- **他分野研究者 / Domain Researcher**  
  - Domain Research field / 他分野研究分野  
  - My supervised master’s thesis topics / 担当修論テーマ
  - TRIOS Research Topics / TRIOS 研究トピック  
  - TRIOS Previous Paper Topics / TRIOS過去論文テーマ

- **AI研究者 / AI Researcher**  
  - AI Research field / AI研究分野  
  - My supervised master’s thesis topics / 担当修論テーマ
  - TRIOS Research Topics / TRIOS 研究トピック  
  - TRIOS Previous Paper Topics / TRIOS過去論文テーマ

---

**A+：一致ボーナス / Match Bonus**  
Aの入力データに完全一致する語が1つでもある場合、総合類似度に+0.01を加算します。  
If there is at least one exact word match in the A input data, +0.01 is added to the overall similarity score.

※ 部分一致は含みません。  
For example, partial matches are not counted.
""")
colw1, colw2, colw3 = st.columns(3)

with colw1:
    weight_a_main = st.number_input(
        "A：AI研究分野 / AI Research Area",
        min_value=0.0,
        max_value=1.0,
        value=float(weight_a),
        step=0.05,
        format="%.2f",
        key="weight_a_main",
    )

with colw2:
    weight_b_main = st.number_input(
        "B：AI研究内容 / AI Research Content",
        min_value=0.0,
        max_value=1.0,
        value=float(weight_b),
        step=0.05,
        format="%.2f",
        key="weight_b_main",
    )

with colw3:
    weight_c_main = st.number_input(
        "C：自身の研究 / Own Research",
        min_value=0.0,
        max_value=1.0,
        value=float(weight_c),
        step=0.05,
        format="%.2f",
        key="weight_c_main",
    )

weight_a = float(weight_a_main)
weight_b = float(weight_b_main)
weight_c = float(weight_c_main)

# ------------------------
# Fast UI: pick person (from ALL) -> show opposite side
# ------------------------
st.markdown('<div id="person_selectbox"></div>', unsafe_allow_html=True)
st.markdown(
    '### 人物を選択 / People search <small>（名前を入力してください / Type a name）</small>',
    unsafe_allow_html=True
)

st.markdown(
    """
    <style>
    div[data-baseweb="select"] { width: 100% !important; font-size: 18px; }
    </style>
    """,
    unsafe_allow_html=True
)

def role_jp(role_norm: str) -> str:
    return "AI研究者 / AI researcher" if role_norm == "ai_researcher" else "他分野研究者 / Domain researcher"

# id → 表示文字
id_to_label = {
    r["id"]: (
        f'👤 {r["name"]} ｜ '
        f'{r.get("affiliation","")} ｜ '
        f'{r.get("position","")} ｜ '
        f'{r.get("research_field","")} ｜ '
        f'【{role_jp(r.get("role_norm",""))}】'
    )
    for _, r in df.iterrows()
}

# ✅ 先頭に None（ダミー）
options = [None] + list(id_to_label.keys())

selected_id_from_query = get_selected_id_from_query()

default_option_index = 0
if selected_id_from_query in options:
    default_option_index = options.index(selected_id_from_query)

# URLの selected_id は「初回だけ」selectbox に反映する
# これで通常の選択操作は上書きされず、
# アンケート画面から ?selected_id=... 付きで戻ったときだけ元の人を復元できる
if "person_selectbox_initialized" not in st.session_state:
    if selected_id_from_query in options:
        st.session_state["person_selectbox"] = selected_id_from_query
    else:
        st.session_state["person_selectbox"] = None
    st.session_state["person_selectbox_initialized"] = True

def format_func(_id):
    if _id is None:
        return "🔍(名前入力 / Type name)"
    return id_to_label[_id]

picked_id = st.selectbox(
    "研究者リスト / Researcher list ※「🔍(名前入力 / Type name)」は消して入力してください / delete the ”🔍(名前入力 / Type name)” and type to search",
    options=options,
    format_func=format_func,
    key="person_selectbox",
)

if picked_id is not None:
    try:
        st.query_params["selected_id"] = str(picked_id)
    except Exception:
        pass

# 未選択なら止める
if picked_id is None:
    st.stop()

# 重み正規化（合計が1でなくても比率として扱う）
weight_sum = float(weight_a + weight_b + weight_c)
if weight_sum <= 0:
    st.error("重みの合計が0です。少なくとも1つを正にしてください。 / Sum of weights is 0. Please set at least one positive.")
    st.stop()

wa = float(weight_a / weight_sum)
wb = float(weight_b / weight_sum)
wc = float(weight_c / weight_sum)

# 選択後
picked = df[df["id"] == picked_id].iloc[0]
st.markdown("### 研究者区分の変更 / Change researcher category")

role_options = {
    "AI researcher": "ai_researcher",
    "Domain researcher": "other_field_researcher",
}
reverse_role_options = {v: k for k, v in role_options.items()}

current_role = normalize_role_value(picked["role_norm"])
current_label = reverse_role_options.get(current_role, "Domain researcher")

new_role_label = st.selectbox(
    "区分を選択",
    options=list(role_options.keys()),
    index=list(role_options.keys()).index(current_label),
    key=f"edit_role_{picked['id']}",
)

if st.button("この変更を保存 / Save this change", key=f"save_role_{picked['id']}"):
    updated_overrides = load_role_overrides()

    person_key = picked.get("person_key")
    if not person_key:
        st.error("person_key が見つかりません。records 側に person_key を追加してください。")
        st.stop()

    actor_name = picked.get("name", picked["id"])

    updated_overrides[person_key] = role_options[new_role_label]

    save_role_overrides_local(updated_overrides)

    synced, sync_message = sync_role_overrides_to_github(
        updated_overrides,
        actor_name=actor_name,
        person_key=person_key,
    )

    if synced:
        st.success(sync_message)
    else:
        st.warning(sync_message)

    st.rerun()

picked_role = picked["role_norm"]

if picked_role == "ai_researcher":
    query_df = ai_df
    doc_df = other_df

    sim_a_matrix = mats["sim_ai_to_other_a"]  # [n_ai, n_other]
    sim_b_matrix = mats["sim_ai_to_other_b"]  # [n_ai, n_other]
    sim_c_matrix = mats["sim_ai_to_other_c"]  # [n_ai, n_other]

    query_label = "AI研究者 / AI researcher"
    doc_label = "他分野研究者 / Domain researcher"
    sel_idx = int(ai_df.index[ai_df["id"] == picked_id][0])
else:
    query_df = other_df
    doc_df = ai_df

    sim_a_matrix = mats["sim_other_to_ai_a"]  # [n_other, n_ai]
    sim_b_matrix = mats["sim_other_to_ai_b"]  # [n_other, n_ai]
    sim_c_matrix = mats["sim_other_to_ai_c"]  # [n_other, n_ai]

    query_label = "他分野研究者 / Domain researcher"
    doc_label = "AI研究者 / AI researcher"
    sel_idx = int(other_df.index[other_df["id"] == picked_id][0])

# ==============================
# 入力データ枠スタート
# ==============================
from streamlit_extras.stylable_container import stylable_container

with stylable_container(
    key="input_data_box",
    css_styles="""
    {
        border: 2px solid #4A90E2;
        border-radius: 12px;
        padding: 20px;
        background-color: #F8FAFF;
        margin-bottom: 20px;
    }
    """
):
    row = query_df.iloc[sel_idx]

    st.write(f"#### {row.get('name','')}さんの入力データ / Input Data for {row.get('name','')}")

    st.write("")
    col1, col2, col3, col4 = st.columns(4)

    with col1:
        st.markdown(f"**名前 / Name**<br>{row.get('name','')}", unsafe_allow_html=True)

    with col2:
        st.markdown(f"**研究者区分 / Role**<br>{query_label}", unsafe_allow_html=True)

    with col3:
        preview_url = str(row.get("streamlit_preview_url", "") or "").strip()

    # row の id ではなく、今一覧で選んでいる picked_id を使う
        current_selected_id = str(picked_id or "").strip()

        if preview_url:
            sep = "&" if "?" in preview_url else "?"
            preview_url_with_state = f"{preview_url}{sep}selected_id={current_selected_id}"
            st.markdown(
                f'**アンケート / Survey**<br><a href="{preview_url_with_state}" target="_self" rel="noopener noreferrer">見る / View</a>',
                unsafe_allow_html=True
            )
        else:
            st.markdown("**アンケート / Survey**<br>なし / None", unsafe_allow_html=True)

    with col4:
        trios = row.get("matched_url", "")
        if pd.notna(trios) and str(trios).strip():
            st.markdown(
                f'**TRIOS URL**<br><a href="{trios}" target="_blank">見る / Open</a>',
                unsafe_allow_html=True
            )
        else:
            st.markdown("**TRIOS URL**<br>なし / None", unsafe_allow_html=True)

    theses = row.get("masters_thesis_titles", [])
    st.markdown(
        "<br><b>担当修論 / Supervised Master's Theses</b><br>"
        + ("<br>".join(f"・{t}" for t in theses) if theses else "なし / None"),
        unsafe_allow_html=True
    )

    embed_text = str(row.get("embed_text", ""))
    st.write("**embed_text 文字数 / Length:**", len(embed_text))
    st.text_area(
        "embed_text（類似度計算に使った全文 / Full text used for similarity）",
        embed_text,
        height=300
    )

st.write("### 現在の重み / Current Weights")
st.write(
    f"A：AI研究分野 / AI Research Area = **{wa:.3f}**　"
    f"B：AI研究内容 / AI Research Content = **{wb:.3f}**　"
    f"C：自身の研究 / Own Research = **{wc:.3f}**"
)

raw_sims_a = sim_a_matrix[sel_idx].astype(np.float32)
raw_sims_b = sim_b_matrix[sel_idx].astype(np.float32)
raw_sims_c = sim_c_matrix[sel_idx].astype(np.float32)

query_has_a = bool(query_df.iloc[sel_idx]["has_a"])
query_has_b = bool(query_df.iloc[sel_idx]["has_b"])
query_has_c = bool(query_df.iloc[sel_idx]["has_c"])

doc_has_a = doc_df["has_a"].astype(bool).to_numpy()
doc_has_b = doc_df["has_b"].astype(bool).to_numpy()
doc_has_c = doc_df["has_c"].astype(bool).to_numpy()

sims_a = raw_sims_a.copy()
sims_b = raw_sims_b.copy()
sims_c = raw_sims_c.copy()

# どちらかが空なら 0.6
sims_a[~(query_has_a & doc_has_a)] = 0.6
sims_b[~(query_has_b & doc_has_b)] = 0.6
sims_c[~(query_has_c & doc_has_c)] = 0.6

# A+ 完全一致ボーナス
query_a_raw_items = query_df.iloc[sel_idx]["a_raw_items"]

matched_words_list = []
a_plus_bonus = []

for _, doc_row in doc_df.iterrows():
    doc_items = doc_row["a_raw_items"]
    matched_words = exact_match_words_between_a(query_a_raw_items, doc_items)

    if len(matched_words) >= 1:
        matched_words_list.append(", ".join(matched_words))
        a_plus_bonus.append(0.01)
    else:
        matched_words_list.append("なし")
        a_plus_bonus.append(0.0)

matched_words_arr = np.asarray(matched_words_list, dtype=object)
a_plus_bonus_arr = np.asarray(a_plus_bonus, dtype=np.float32)
# 総合類似度
sims = (wa * sims_a + wb * sims_b + wc * sims_c + a_plus_bonus_arr).astype(np.float32)
order_idx = np.argsort(-sims)


res = doc_df.iloc[order_idx].copy()
res.insert(0, "rank", np.arange(1, len(res) + 1))
res.insert(1, "similarity_a", sims_a[order_idx].astype(float))
res.insert(2, "matched_words", matched_words_arr[order_idx])
res.insert(3, "similarity_b", sims_b[order_idx].astype(float))
res.insert(4, "similarity_c", sims_c[order_idx].astype(float))
res.insert(5, "similarity", sims[order_idx].astype(float))
show_cols = [
    "rank",
    "similarity_a",
    "matched_words",
    "similarity_b",
    "similarity_c",
    "similarity",
    "id",
    "name",
    "affiliation",
    "position",
    "research_field",
    "summary",
    "matched_url",
    "streamlit_preview_url",
]
res_show = res[show_cols].copy()

def add_selected_id(url, selected_id):
    url = str(url or "").strip()
    selected_id = str(selected_id or "").strip()
    if not url or not selected_id:
        return url
    sep = "&" if "?" in url else "?"
    return f"{url}{sep}selected_id={selected_id}"

# まず元の列 streamlit_preview_url に selected_id を付ける
if "streamlit_preview_url" in res_show.columns:
    res_show["streamlit_preview_url"] = [
        add_selected_id(u, picked_id)
        for u in res_show["streamlit_preview_url"]
    ]

# そのあとで表示用に列名を変える
if "streamlit_preview_url" in res_show.columns:
    res_show = res_show.rename(columns={"streamlit_preview_url": "survey_url"})

download_df = res_show.copy()

# ダウンロード用では、アンケートURL列をわかりやすく統一
if "streamlit_preview_url" in download_df.columns:
    download_df["survey_url"] = download_df["streamlit_preview_url"]
elif "url" in download_df.columns:
    download_df["survey_url"] = download_df["url"]
else:
    download_df["survey_url"] = ""

# 元の内部用列はダウンロードから外す
drop_cols = []
for c in ["streamlit_preview_url"]:
    if c in download_df.columns:
        drop_cols.append(c)

if drop_cols:
    download_df = download_df.drop(columns=drop_cols)

csv_bytes = download_df.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")
json_bytes = download_df.to_json(orient="records", force_ascii=False, indent=2).encode("utf-8")

st.subheader(f"検索結果 / Results list （推薦 / Recommendation : {doc_label})　　件数 / Count : {len(res_show)}")
st.caption(f"表示 / Direction : {query_label} → {doc_label}")
st.caption("※ 入力データが一致している場合は、類似度に +0.01 されます。 / If the input data matches exactly, +0.01 is added to the similarity score.")
st.caption("※ 入力データがない場合は類似度が0.6になります。 / If no input data is available, the similarity score is set to 0.6.")

try:
    st.dataframe(
        res_show,
        use_container_width=True,
        height=700,
        column_config={
            "survey_url": st.column_config.LinkColumn(
                "アンケート表示 / Survey Preview",
                display_text="open"
            ),
            "matched_url": st.column_config.LinkColumn(
                "TRIOS URL",
                display_text="open"
            ),
            "similarity_a": st.column_config.NumberColumn("A", format="%.4f"),
            "matched_words": st.column_config.TextColumn("一致ワード / Matched Words"),
            "similarity_b": st.column_config.NumberColumn("B", format="%.4f"),
            "similarity_c": st.column_config.NumberColumn("C", format="%.4f"),
            "similarity": st.column_config.NumberColumn(
                "総合類似度 / Overall Similarity",
                format="%.4f"
            ),
            "rank": st.column_config.NumberColumn("順位 / Rank"),
        },
        hide_index=True,
    )
except Exception:
    st.dataframe(res_show, use_container_width=True, height=700, hide_index=True)


st.caption(f"使用モデル / Model : {DEFAULT_MODEL}")

# ---- ダウンロードも全件 ----
def safe_filename(s: str) -> str:
    s = (s or "").strip()
    return re.sub(r'[\\/:*?"<>|]+', "_", s) or "unknown"

picked_name = safe_filename(str(row.get("name", "")))

st.download_button(
    "結果（全件）をCSVでダウンロード / Download all results as CSV",
    data=csv_bytes,
    file_name=f"match_results_all_{picked_name}.csv",
    mime="text/csv",
)

st.download_button(
    "結果（全件）をJSONでダウンロード / Download all results as JSON",
    data=json_bytes,
    file_name=f"match_results_all_{picked_name}.json",
    mime="application/json",
)