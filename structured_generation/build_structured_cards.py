#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Build structured cards (JSONL + XLSX) from the AI for Science survey export.

Features:
- No ID columns in outputs (no ID / respondent_id / submission_id / card_id / etc.)
- Emails INCLUDED (meta.email)
- Names INCLUDED (meta.name + meta.name_raw)
- Domain projects split into multiple theme rows in project_cards
- Deterministic extraction using pattern/code mapping (no external LLM required)

Usage:
  python build_structured_cards.py \
    --input "AI for Science「チャレンジ型」公募に向けたアンケート調査　　　　　(1-260).xlsx" \
    --out-jsonl "cards_1-260.jsonl" \
    --out-xlsx  "cards_1-260.xlsx"
"""

from __future__ import annotations

import argparse
import json
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


# -----------------------------
# Utilities
# -----------------------------

def clean_text(x: Any) -> Optional[str]:
    """Normalize cell value to clean text; return None if empty/NaN."""
    if x is None:
        return None
    if isinstance(x, float) and math.isnan(x):
        return None
    s = str(x)
    if s.strip().lower() in ("nan", ""):
        return None
    s = s.replace("\r\n", "\n").replace("\r", "\n").strip()
    return s if s else None


def split_multiselect(x: Any) -> List[str]:
    """Split semicolon-delimited multi-select field."""
    s = clean_text(x)
    if not s:
        return []
    s = s.strip().strip(";")
    parts = [p.strip() for p in s.split(";")]
    return [p for p in parts if p]


def uniq_preserve(lst: List[str]) -> List[str]:
    """De-duplicate list while preserving order."""
    seen = set()
    out: List[str] = []
    for x in lst:
        if x not in seen:
            out.append(x)
            seen.add(x)
    return out


def prune_nones(obj: Any) -> Any:
    """Recursively drop None / empty dict / empty list to keep JSON compact."""
    if isinstance(obj, dict):
        new = {}
        for k, v in obj.items():
            pv = prune_nones(v)
            if pv is None:
                continue
            new[k] = pv
        return new if new else None
    if isinstance(obj, list):
        new = [prune_nones(v) for v in obj]
        new = [v for v in new if v is not None]
        return new if new else None
    return obj


def find_col(
    df: pd.DataFrame,
    *,
    startswith: Optional[str] = None,
    contains_all: Optional[List[str]] = None,
    contains_any: Optional[List[str]] = None,
    required: bool = True,
) -> Optional[str]:
    """
    Find a column name robustly.
    - startswith: prefix match (e.g., "3-1")
    - contains_all: all tokens must be in column name
    - contains_any: at least one token must be in column name
    """
    cols = list(df.columns)
    candidates = cols

    if startswith is not None:
        candidates = [c for c in candidates if str(c).startswith(startswith)]
    if contains_all is not None:
        candidates = [c for c in candidates if all(tok in str(c) for tok in contains_all)]
    if contains_any is not None:
        candidates = [c for c in candidates if any(tok in str(c) for tok in contains_any)]

    if not candidates:
        if required:
            raise KeyError(
                f"Could not find column (startswith={startswith}, "
                f"contains_all={contains_all}, contains_any={contains_any})"
            )
        return None

    # Prefer shortest header if multiple candidates exist (less likely to be duplicated variants).
    candidates = sorted(candidates, key=lambda x: len(str(x)))
    return candidates[0]


# -----------------------------
# Domain-specific mappings
# -----------------------------

def is_junk_theme(theme: Optional[str]) -> bool:
    """Detect obvious junk placeholders like 'ああああああ'."""
    s = clean_text(theme)
    if not s:
        return False
    if len(s) <= 10:
        if len(set(s)) <= 2:
            return True
        if re.fullmatch(r"(.)\1{5,}", s):
            return True
    if s.lower() in ("test", "aaa", "aaaa"):
        return True
    return False


def map_challenge(opt: str) -> Optional[str]:
    """Map challenge selection text -> normalized code."""
    s = opt

    if "相談できる相手がいない" in s or "伝手がない" in s:
        return "no_ai_contacts"
    if "合うか判断できない" in s:
        return "fit_unclear"
    if "研究データはあるが" in s or "AIに使えるかわからない" in s:
        return "data_usability_unclear"
    if "どこからはじめればよいかわからない" in s or "何ができるのか" in s:
        return "dont_know_start"
    if "データ整理" in s or "前処理" in s:
        return "preprocessing_hard"
    if "どう評価されるかわからない" in s or "評価される" in s:
        return "eval_in_field_unclear"
    if "研究倫理" in s or "倫理観" in s:
        return "ethics_concerns"
    if "学生" in s and "人材" in s:
        return "no_local_talent"
    if "自分の研究にAIが使えるかわからない" in s:
        return "ai_applicability_unclear"
    if "研究構想が浮かばない" in s:
        return "no_idea"

    # extra free-text patterns (optional):
    if re.search(r"計算資源|GPU|ハードウェア|計算能力|compute", s, flags=re.I):
        return "compute_limits"
    if re.search(r"投稿規定|journal|publisher", s, flags=re.I):
        return "publication_constraints"
    if re.search(r"研究費|有料版|コスト|budget|cost", s, flags=re.I):
        return "budget"

    return None


def map_complexity(opt: str) -> Optional[str]:
    """Map complexity selection text -> normalized code."""
    s = opt
    if "時系列" in s or "Time-series" in s:
        return "time_series"
    if "多変量" in s or "Multivariate" in s:
        return "multivariate"
    if "線形" in s or "Nonlinear" in s:
        return "linear_nonlinear"
    if "マルチモーダル" in s or "Multimodal" in s:
        return "multimodal"
    if "階層" in s or "Hierarchical" in s:
        return "hierarchical"
    if "次元" in s or "High dimensionality" in s:
        return "high_dimensional"
    return None


def map_ai_category(opt: str) -> Optional[str]:
    """Map AI category selection text -> normalized code."""
    s = opt
    if "機械学習" in s or "Machine Learning" in s:
        return "machine_learning"
    if "言語メディア" in s or "Language and Media" in s:
        return "language_media"
    if "エージェント" in s or "Agents" in s:
        return "agents"
    if "知識の利用と共有" in s or "Knowledge Utilization" in s:
        return "knowledge_sharing"
    if "ヒューマンインタフェース" in s or "Human Interfaces" in s:
        return "human_interface"
    if "AIと社会" in s or "AI and Society" in s:
        return "ai_society"
    if "AI応用" in s or "AI Applications" in s:
        return "ai_applications"
    if "Webインテリジェンス" in s or "Web Intelligence" in s:
        return "web_intelligence"
    if "医療技術" in s or "Medical" in s:
        return "medical_ai"
    if "基礎・理論" in s or "Foundations" in s or "Theory" in s:
        return "foundations_theory"
    if "ロボティクス" in s or "Robotics" in s:
        return "robotics_realworld"
    if "システム構築" in s or "加速チップ" in s:
        return "systems_accelerator_chip"
    if "image and video generation" in s.lower() or "image generation" in s.lower() or "video generation" in s.lower():
        return "image_video_generation"
    return None


def detect_modalities(*texts: Optional[str]) -> List[str]:
    """Heuristic modality detection from free text."""
    t = " ".join([clean_text(x) or "" for x in texts]).strip()
    if not t:
        return []
    mods = set()

    if re.search(r"画像|image|写真|顕微鏡|microscope", t, flags=re.I):
        mods.add("image")
    if re.search(r"動画|video|映像", t, flags=re.I):
        mods.add("video")
    if re.search(r"音声|audio|speech|声", t, flags=re.I):
        mods.add("audio")
    if re.search(r"テキスト|text|自然言語|NLP|文献|論文|paper", t, flags=re.I):
        mods.add("text")
    if re.search(r"時系列|time[- ]?series", t, flags=re.I):
        mods.add("time_series")
    if re.search(r"シミュレーション|simulation", t, flags=re.I):
        mods.add("simulation")
    if re.search(r"センサ|sensor", t, flags=re.I):
        mods.add("sensor")
    if re.search(r"行動|behavior|behaviour", t, flags=re.I):
        mods.add("behavior")
    if re.search(r"アンケート|社会調査|survey|questionnaire", t, flags=re.I):
        mods.add("survey")
    if re.search(r"アーカイブ|archive|デジタルアーカイブ", t, flags=re.I):
        mods.add("archive")
    if re.search(r"グラフ|ネットワーク|graph|network", t, flags=re.I):
        mods.add("graph")
    if re.search(r"測定|数値|tabular|表|dataset|観測|実験", t, flags=re.I):
        mods.add("tabular")

    return sorted(mods)


TASK_PATTERNS = [
    (r"分類|classification", "classification"),
    (r"回帰|regression", "regression"),
    (r"予測|prediction|forecast", "prediction"),
    (r"生成|generation|generate", "generation"),
    (r"最適化|optimization|optimi[sz]e", "optimization"),
    (r"クラスタ|clustering|cluster", "clustering"),
    (r"異常|anomal|outlier", "anomaly_detection"),
    (r"因果|causal", "causal_inference"),
    (r"強化学習|reinforcement", "reinforcement_learning"),
    (r"推薦|recommend", "recommendation"),
    (r"検索|retrieval", "retrieval"),
    (r"要約|summari", "summarization"),
    (r"解釈|explain|説明可能", "interpretability"),
]


def detect_task_types(*texts: Optional[str]) -> List[str]:
    t = " ".join([clean_text(x) or "" for x in texts])
    if not t.strip():
        return []
    res = set()
    for pat, code in TASK_PATTERNS:
        if re.search(pat, t, flags=re.I):
            res.add(code)
    return sorted(res)


def detect_constraints(*texts: Optional[str]) -> List[str]:
    t = " ".join([clean_text(x) or "" for x in texts])
    if not t.strip():
        return []
    res = set()
    if re.search(r"個人情報|プライバシ|privacy", t, flags=re.I):
        res.add("privacy")
    if re.search(r"倫理|IRB|human subject|被験者|臨床|同意", t, flags=re.I):
        res.add("ethics_human_subjects")
    if re.search(r"権利|知財|IP|著作権|intellectual property|二次利用|ライセンス", t, flags=re.I):
        res.add("ip")
    if re.search(r"計算資源|GPU|ハードウェア|compute|計算能力", t, flags=re.I):
        res.add("compute_limits")
    if re.search(r"公開できない|機密|confidential", t, flags=re.I):
        res.add("confidentiality")
    if re.search(r"投稿規定|journal|publisher", t, flags=re.I):
        res.add("publication_constraints")
    return sorted(res)


METHOD_PATTERNS = [
    (r"LLM|大規模言語モデル|language model", "llm"),
    (r"強化学習|reinforcement", "reinforcement_learning"),
    (r"マルチエージェント|multi[- ]?agent", "multi_agent"),
    (r"グラフニューラル|GNN|graph neural", "graph_neural_network"),
    (r"拡散|diffusion", "diffusion"),
    (r"生成|generat", "generative_model"),
    (r"因果|causal", "causal_inference"),
    (r"ベイズ|bayes", "bayesian"),
    (r"時系列|time[- ]?series", "time_series"),
    (r"画像|vision|computer vision", "computer_vision"),
    (r"自然言語|NLP|言語処理", "nlp"),
    (r"最適化|optimization", "optimization"),
    (r"説明可能|解釈|interpret", "interpretability"),
    (r"フェデレーテッド|federated", "federated_learning"),
    (r"転移学習|transfer", "transfer_learning"),
    (r"メタ学習|meta[- ]?learning", "meta_learning"),
    (r"表現学習|representation", "representation_learning"),
    (r"ロボット|robot", "robotics"),
]


def extract_methods(text: Optional[str]) -> List[str]:
    t = clean_text(text) or ""
    if not t.strip():
        return []
    res = set()
    for pat, code in METHOD_PATTERNS:
        if re.search(pat, t, flags=re.I):
            res.add(code)
    return sorted(res)


def normalize_theme_list(theme_text: Optional[str]) -> List[str]:
    """Split multiple themes (newlines/semicolons) and remove numbering/bullets."""
    s = clean_text(theme_text)
    if not s:
        return []
    lines = [ln.strip() for ln in s.replace("\t", " ").split("\n")]
    parts: List[str] = []
    for ln in lines:
        if not ln:
            continue
        for p in ln.split(";"):
            p = p.strip()
            if p:
                parts.append(p)

    cleaned: List[str] = []
    for p in parts:
        p = re.sub(r"^[\s　]*(?:[①②③④⑤⑥⑦⑧⑨⑩]|[0-9]+[)\]）]|[０-９]+[)\]）]|[・•\-–—]+)\s*", "", p)
        p = re.sub(r"^[\s　]*[0-9]+[\.．]\s*", "", p)
        p = p.strip(" \u3000")
        if p:
            cleaned.append(p)

    return uniq_preserve(cleaned)


def map_ready_state(val: Optional[str]) -> Tuple[str, Optional[str]]:
    s = clean_text(val)
    if not s:
        return ("unknown", None)
    if "取得途中" in s or "currently being collected" in s:
        return ("collecting", s)
    if "既に取得済み" in s or "already been collected" in s:
        return ("collected", s)
    if "これから" in s or "not yet been collected" in s or "prospective" in s:
        return ("planned", s)
    return ("unknown", s)


def classify_role(ai_exp: Optional[str], ai_cat_raw: Optional[str], ai_theme: Optional[str]) -> str:
    """
    Role heuristic:
    - If 2-1 says "AIそのもの/高度化" => AI_researcher
    - Else if 5-1 or 5-2 present => AI_researcher
    - Else => Domain_researcher
    """
    s = clean_text(ai_exp) or ""
    if "AIそのもの" in s or "高度化" in s:
        return "AI_researcher"
    if clean_text(ai_cat_raw) or clean_text(ai_theme):
        return "AI_researcher"
    return "Domain_researcher"


def needed_ai_hints(modalities: List[str], task_types: List[str], project_text: str, field_text: str) -> List[str]:
    """Conservative AI category hints for domain-side projects."""
    t = f"{project_text} {field_text}"
    hints = set()

    if modalities:
        hints.add("machine_learning")
    if any(m in ("text", "image", "video", "audio") for m in modalities):
        hints.add("language_media")
    if re.search(r"医療|臨床|病院|診断|患者|medical|clinical|health", t, flags=re.I):
        hints.add("medical_ai")
    if re.search(r"法|法律|制度|責任|消費者|policy|law|ethic|倫理", t, flags=re.I):
        hints.add("ai_society")
    if re.search(r"エージェント|agent|強化学習|reinforcement", t, flags=re.I):
        hints.add("agents")
    if re.search(r"知識|ナレッジ|knowledge|ontology|graph", t, flags=re.I):
        hints.add("knowledge_sharing")
    if re.search(r"Web|SNS|ソーシャル|recommend|推薦|検索|retrieval", t, flags=re.I):
        hints.add("web_intelligence")
    if re.search(r"ロボット|robot", t, flags=re.I):
        hints.add("robotics_realworld")
    if "generation" in task_types or "summarization" in task_types:
        hints.add("language_media")

    return sorted(hints)


def compute_quality(
    themes: List[str],
    problem: Optional[str],
    modalities: List[str],
    role: str,
    offers_present: bool,
    junk: bool,
    duplicate_email: bool,
) -> Tuple[float, List[str]]:
    """Simple quality scoring with flags."""
    flags: List[str] = []
    score = 1.0

    if junk:
        flags.append("junk_theme")
        score -= 0.9
    if not themes:
        flags.append("missing_theme")
        score -= 0.25
    if not problem:
        flags.append("missing_problem")
        score -= 0.25
    if role == "Domain_researcher" and not modalities:
        flags.append("missing_data_desc")
        score -= 0.2
    if role == "AI_researcher" and not offers_present:
        flags.append("missing_ai_offers")
        score -= 0.2
    if duplicate_email:
        flags.append("duplicate_email")
        score -= 0.05

    score = max(0.0, min(1.0, score))
    return score, flags


# -----------------------------
# Main processing
# -----------------------------

def process_survey_excel(input_xlsx: str, out_jsonl: str, out_xlsx: str) -> Dict[str, Any]:
    df = pd.read_excel(input_xlsx, sheet_name=0)

    # Required/expected columns (found robustly by prefix/contains)
    col_email = find_col(df, contains_any=["メール", "Email"])
    col_name_full = find_col(df, contains_all=["氏名", "Name"], required=False)
    col_name = find_col(df, startswith="名前", required=False) or "名前"
    col_aff = find_col(df, contains_any=["所属", "Affiliation"])
    col_field = find_col(df, startswith="専門分野")
    col_position = find_col(df, contains_any=["職位", "Position"], required=False)
    col_select = find_col(df, contains_any=["以下の項目から選んでください", "Please select"], required=False)
    col_lang = find_col(df, contains_any=["言語", "Language"], required=False)

    col_supervisor = find_col(df, contains_any=["指導教員名", "Supervisor"], required=False)
    col_supervisor_confirm = find_col(df, contains_any=["確認しましたか", "confirmed"], required=False)

    col_ai_exp = find_col(df, startswith="2－1", required=False) or find_col(df, startswith="2-1", required=False)
    col_theme = find_col(df, startswith="3-1")
    col_problem = find_col(df, startswith="3-2")
    col_leverage = find_col(df, startswith="3-3", required=False)
    col_apply = find_col(df, startswith="3-4", required=False)
    col_challenges = find_col(df, startswith="3-5", required=False)
    col_ready = find_col(df, startswith="3-6", required=False)

    col_sources = find_col(df, startswith="4-1", required=False)
    col_datatype = find_col(df, startswith="4-2", required=False)
    col_sources_other = find_col(df, startswith="4-3", required=False)
    col_datatype_other = find_col(df, startswith="4-4", required=False)
    col_basic = find_col(df, startswith="4-5", required=False)
    col_methods_applied = find_col(df, startswith="4-6", required=False)
    col_rel = find_col(df, startswith="4-7", required=False)
    col_bias = find_col(df, startswith="4-8", required=False)
    col_complexity = find_col(df, startswith="4-9", required=False)

    col_ai_cat = find_col(df, startswith="5-1", required=False)
    col_ai_theme = find_col(df, startswith="5-2", required=False)

    # Optional timestamps
    col_started = find_col(df, contains_any=["開始時刻", "Start"], required=False)
    col_completed = find_col(df, contains_any=["完了時刻", "Completed"], required=False)
    col_modified = find_col(df, contains_any=["最終変更時刻", "Last modified"], required=False)

    # Duplicate detection by email
    emails = df[col_email].apply(clean_text).fillna("")
    dup_email_set = set(df.loc[emails.duplicated(keep=False) & (emails != ""), col_email].tolist())

    cards: List[Dict[str, Any]] = []
    project_cards: List[Dict[str, Any]] = []

    for _, row in df.iterrows():
        email = clean_text(row.get(col_email))
        name_full = clean_text(row.get(col_name_full)) if col_name_full else None
        name_simple = clean_text(row.get(col_name)) if col_name else None
        display_name = name_full or name_simple

        respondent_type = clean_text(row.get(col_select)) if col_select else None
        affiliation = clean_text(row.get(col_aff))
        field = clean_text(row.get(col_field))
        position = clean_text(row.get(col_position)) if col_position else None
        language = clean_text(row.get(col_lang)) if col_lang else None

        ai_exp = clean_text(row.get(col_ai_exp)) if col_ai_exp else None
        ai_cat_raw = clean_text(row.get(col_ai_cat)) if col_ai_cat else None
        ai_theme = clean_text(row.get(col_ai_theme)) if col_ai_theme else None

        role = classify_role(ai_exp, ai_cat_raw, ai_theme)

        theme_raw = clean_text(row.get(col_theme))
        themes = normalize_theme_list(theme_raw)
        problem = clean_text(row.get(col_problem))
        leverage = clean_text(row.get(col_leverage)) if col_leverage else None
        apply_intent = clean_text(row.get(col_apply)) if col_apply else None

        ready_state, ready_raw = map_ready_state(clean_text(row.get(col_ready)) if col_ready else None)

        sources = clean_text(row.get(col_sources)) if col_sources else None
        data_type_raw = clean_text(row.get(col_datatype)) if col_datatype else None
        sources_other = clean_text(row.get(col_sources_other)) if col_sources_other else None
        data_type_other_raw = clean_text(row.get(col_datatype_other)) if col_datatype_other else None
        basic_info = clean_text(row.get(col_basic)) if col_basic else None
        methods_applied = clean_text(row.get(col_methods_applied)) if col_methods_applied else None
        rel_notes = clean_text(row.get(col_rel)) if col_rel else None
        bias_notes = clean_text(row.get(col_bias)) if col_bias else None

        complexity_raw_list = split_multiselect(row.get(col_complexity)) if col_complexity else []
        complexity_flags = uniq_preserve([c for c in (map_complexity(o) for o in complexity_raw_list) if c])

        challenges_raw_list = split_multiselect(row.get(col_challenges)) if col_challenges else []
        challenge_codes: List[str] = []
        other_challenges: List[str] = []
        for opt in challenges_raw_list:
            code = map_challenge(opt)
            if code:
                challenge_codes.append(code)
            else:
                other_challenges.append(opt)
        challenge_codes = uniq_preserve(challenge_codes)
        other_challenges = uniq_preserve(other_challenges)

        modalities = detect_modalities(
            data_type_raw,
            data_type_other_raw,
            sources,
            sources_other,
            basic_info,
            theme_raw,
            problem,
            leverage,
        )
        task_types = detect_task_types(problem, leverage, data_type_raw, data_type_other_raw)
        constraints = detect_constraints(problem, leverage, sources, rel_notes, bias_notes, ";".join(other_challenges))

        # AI offers parsing
        ai_categories_raw_list = split_multiselect(row.get(col_ai_cat)) if col_ai_cat else []
        ai_categories_codes: List[str] = []
        ai_categories_other: List[str] = []
        for opt in ai_categories_raw_list:
            code = map_ai_category(opt)
            if code:
                ai_categories_codes.append(code)
            else:
                ai_categories_other.append(opt)
        ai_categories_codes = uniq_preserve(ai_categories_codes)
        ai_categories_other = uniq_preserve(ai_categories_other)

        methods_keywords = extract_methods(ai_theme) if role == "AI_researcher" else []
        offers_present = bool(ai_categories_codes or ai_theme or methods_keywords)

        junk = is_junk_theme(theme_raw)
        duplicate_email = (email in dup_email_set) if email else False
        q_score, q_flags = compute_quality(themes, problem, modalities, role, offers_present, junk, duplicate_email)

        # One-line pitch
        if role == "AI_researcher":
            pitch = ai_theme or (themes[0] if themes else None) or field
        else:
            pitch = (themes[0] if themes else None) or field

        # Canonical embedding-ready text
        lines = [f"Role: {role.replace('_', ' ')}"]
        if display_name:
            lines.append(f"Name: {display_name}")
        if email:
            lines.append(f"Email: {email}")
        if affiliation:
            lines.append(f"Affiliation: {affiliation}")
        if field:
            lines.append(f"Research field: {field}")
        if position:
            lines.append(f"Position: {position}")
        if ai_exp:
            lines.append(f"AI experience: {ai_exp}")
        if themes:
            lines.append("Themes:")
            for t in themes[:8]:
                lines.append(f"- {t}")
        if problem:
            lines.append(f"Academic challenge: {problem}")
        if leverage:
            lines.append(f"AI leverage / impact: {leverage}")

        if role == "Domain_researcher":
            lines.append(f"Data readiness: {ready_state}")
            if modalities:
                lines.append(f"Data modalities: {', '.join(modalities)}")
            if complexity_flags:
                lines.append(f"Data complexity: {', '.join(complexity_flags)}")
            if challenge_codes:
                lines.append(f"Reported challenges: {', '.join(challenge_codes)}")
            if constraints:
                lines.append(f"Constraints: {', '.join(constraints)}")
            if task_types:
                lines.append(f"Task hints: {', '.join(task_types)}")
        else:
            if ai_categories_codes:
                lines.append(f"AI categories: {', '.join(ai_categories_codes)}")
            if methods_keywords:
                lines.append(f"Methods keywords: {', '.join(methods_keywords)}")
            if ai_theme:
                lines.append(f"Current AI themes: {ai_theme}")

        canonical_card_text = "\n".join(lines)

        keywords = uniq_preserve(
            (ai_categories_codes if role == "AI_researcher" else [])
            + modalities
            + task_types
            + constraints
            + challenge_codes
        )

        supervisor = None
        if respondent_type and "大学院生" in respondent_type and col_supervisor and col_supervisor_confirm:
            supervisor = {
                "name": clean_text(row.get(col_supervisor)),
                "confirmed": clean_text(row.get(col_supervisor_confirm)),
            }

        timestamps = {}
        if col_started:
            timestamps["started"] = clean_text(row.get(col_started))
        if col_completed:
            timestamps["completed"] = clean_text(row.get(col_completed))
        if col_modified:
            timestamps["last_modified"] = clean_text(row.get(col_modified))
        timestamps = timestamps if timestamps else None

        card: Dict[str, Any] = {
            "meta": {
                "name": display_name,
                "name_raw": {"名前": name_simple, "氏名_Name": name_full},
                "email": email,
                "respondent_type": respondent_type,
                "affiliation": affiliation,
                "research_field": field,
                "position": position,
                "language": language,
                "timestamps": timestamps,
                "supervisor": supervisor,
            },
            "role": role,
            "ai_experience": ai_exp,
            "project": {
                "themes": themes if themes else None,
                "academic_challenge_overview": problem,
                "ai_leverage_and_impact": leverage,
                "apply_intent": apply_intent,
            },
            "data": {
                "ready_state": ready_state if role == "Domain_researcher" else None,
                "ready_state_raw": ready_raw if role == "Domain_researcher" else None,
                "sources_and_collection": sources,
                "data_types_raw": data_type_raw,
                "other_sources_and_collection": sources_other,
                "other_data_types_raw": data_type_other_raw,
                "modalities": modalities if modalities else None,
                "basic_info": basic_info,
                "methods_applied": methods_applied,
                "reliability_notes": rel_notes,
                "bias_notes": bias_notes,
                "complexity_flags": complexity_flags if complexity_flags else None,
                "complexity_raw": complexity_raw_list if complexity_raw_list else None,
            },
            "needs": {
                "survey_challenges": challenge_codes if challenge_codes else None,
                "survey_challenges_raw": challenges_raw_list if challenges_raw_list else None,
                "survey_challenges_other_free_text": other_challenges if other_challenges else None,
                "task_type_hints": task_types if task_types else None,
                "constraints": constraints if constraints else None,
                "needed_ai_category_hints": needed_ai_hints(
                    modalities,
                    task_types,
                    f"{' '.join(themes)} {problem or ''} {leverage or ''}",
                    field or "",
                ) if role == "Domain_researcher" else None,
            } if role == "Domain_researcher" else None,
            "offers": {
                "ai_categories_5_1": ai_categories_codes if ai_categories_codes else None,
                "ai_categories_raw": ai_categories_raw_list if ai_categories_raw_list else None,
                "ai_categories_other": ai_categories_other if ai_categories_other else None,
                "methods_keywords": methods_keywords if methods_keywords else None,
                "current_main_research_themes": ai_theme,
            } if role == "AI_researcher" else None,
            "match_text": {
                "one_line_pitch": pitch,
                "keywords": keywords if keywords else None,
                "canonical_card_text": canonical_card_text,
            },
            "quality": {
                "quality_score": q_score,
                "flags": q_flags if q_flags else None,
            },
            "evidence": {
                "theme_3_1": theme_raw,
                "problem_3_2": problem,
                "leverage_3_3": leverage,
                "data_ready_3_6": ready_raw,
                "data_type_4_2": data_type_raw,
                "data_type_4_4": data_type_other_raw,
                "challenges_3_5": clean_text(row.get(col_challenges)) if col_challenges else None,
                "complexity_4_9": clean_text(row.get(col_complexity)) if col_complexity else None,
                "ai_categories_5_1": ai_cat_raw,
                "ai_theme_5_2": ai_theme,
            },
        }

        card = prune_nones(card) or {}
        cards.append(card)

        # Project cards (theme-split) for domain side
        if role == "Domain_researcher":
            themes_for_projects = themes if themes else [None]
            for theme_no, t in enumerate(themes_for_projects, start=1):
                proj_text = " ".join(
                    [t or "", problem or "", leverage or "", data_type_raw or "", data_type_other_raw or "", sources or "", basic_info or ""]
                ).strip()
                proj_modalities = detect_modalities(t, proj_text)
                proj_task_types = detect_task_types(t, problem, leverage)
                proj_constraints = detect_constraints(proj_text, rel_notes, bias_notes)
                hints = needed_ai_hints(proj_modalities, proj_task_types, proj_text, field or "")

                canonical_project_text = "\n".join([ln for ln in [
                    f"Domain project theme: {t}" if t else "Domain project theme: (missing)",
                    f"Research field: {field}" if field else None,
                    f"Academic challenge: {problem}" if problem else None,
                    f"AI leverage/impact: {leverage}" if leverage else None,
                    f"Data readiness: {ready_state}",
                    f"Modalities: {', '.join(proj_modalities)}" if proj_modalities else None,
                    f"Complexity: {', '.join(complexity_flags)}" if complexity_flags else None,
                    f"Challenges: {', '.join(challenge_codes)}" if challenge_codes else None,
                    f"Task hints: {', '.join(proj_task_types)}" if proj_task_types else None,
                    f"Constraints: {', '.join(proj_constraints)}" if proj_constraints else None,
                    f"Needed AI categories (hints): {', '.join(hints)}" if hints else None,
                ] if ln])

                project_cards.append({
                    "name": display_name,
                    "email": email,
                    "affiliation": affiliation,
                    "research_field": field,
                    "role": role,
                    "theme_no": theme_no,         # NOT an ID; just ordering of themes
                    "theme": t,
                    "academic_challenge_overview": problem,
                    "ai_leverage_and_impact": leverage,
                    "data_ready_state": ready_state,
                    "modalities": ", ".join(proj_modalities) if proj_modalities else None,
                    "complexity_flags": ", ".join(complexity_flags) if complexity_flags else None,
                    "survey_challenges": ", ".join(challenge_codes) if challenge_codes else None,
                    "task_type_hints": ", ".join(proj_task_types) if proj_task_types else None,
                    "constraints": ", ".join(proj_constraints) if proj_constraints else None,
                    "needed_ai_category_hints": ", ".join(hints) if hints else None,
                    "quality_score": q_score,
                    "quality_flags": ", ".join(q_flags) if q_flags else None,
                    "canonical_project_text": canonical_project_text,
                })

    # Write JSONL (complete, not truncated)
    with open(out_jsonl, "w", encoding="utf-8") as f:
        for card in cards:
            f.write(json.dumps(card, ensure_ascii=False) + "\n")

    # Build Excel output
    cards_rows: List[Dict[str, Any]] = []
    for card in cards:
        meta = card.get("meta", {})
        match_text = card.get("match_text", {})
        quality = card.get("quality", {})
        needs = card.get("needs", {}) or {}
        offers = card.get("offers", {}) or {}
        data = card.get("data", {}) or {}

        cards_rows.append({
            "name": meta.get("name"),
            "email": meta.get("email"),
            "role": card.get("role"),
            "respondent_type": meta.get("respondent_type"),
            "affiliation": meta.get("affiliation"),
            "research_field": meta.get("research_field"),
            "position": meta.get("position"),
            "ai_experience": card.get("ai_experience"),
            "one_line_pitch": match_text.get("one_line_pitch"),
            "quality_score": quality.get("quality_score"),
            "quality_flags": ", ".join(quality.get("flags", [])) if quality.get("flags") else None,
            "data_ready_state": data.get("ready_state"),
            "modalities": ", ".join(data.get("modalities", [])) if data.get("modalities") else None,
            "complexity_flags": ", ".join(data.get("complexity_flags", [])) if data.get("complexity_flags") else None,
            "survey_challenges": ", ".join(needs.get("survey_challenges", [])) if needs and needs.get("survey_challenges") else None,
            "needed_ai_category_hints": ", ".join(needs.get("needed_ai_category_hints", [])) if needs and needs.get("needed_ai_category_hints") else None,
            "ai_categories_5_1": ", ".join(offers.get("ai_categories_5_1", [])) if offers and offers.get("ai_categories_5_1") else None,
            "methods_keywords": ", ".join(offers.get("methods_keywords", [])) if offers and offers.get("methods_keywords") else None,
            "canonical_card_text": match_text.get("canonical_card_text"),
            "card_json": json.dumps(card, ensure_ascii=False),
        })

    df_cards_out = pd.DataFrame(cards_rows)
    df_projects_out = pd.DataFrame(project_cards)

    # Truncate long fields for Excel cell limit
    MAX_CELL = 32000
    for col in ("card_json", "canonical_card_text"):
        if col in df_cards_out.columns:
            df_cards_out[col] = df_cards_out[col].astype(str).apply(
                lambda s: s if len(s) <= MAX_CELL else s[: MAX_CELL - 20] + "...(truncated)"
            )
    if "canonical_project_text" in df_projects_out.columns:
        df_projects_out["canonical_project_text"] = df_projects_out["canonical_project_text"].astype(str).apply(
            lambda s: s if len(s) <= MAX_CELL else s[: MAX_CELL - 20] + "...(truncated)"
        )

    stats = {
        "n_submissions": len(cards),
        "n_ai_researchers": sum(1 for c in cards if c.get("role") == "AI_researcher"),
        "n_domain_researchers": sum(1 for c in cards if c.get("role") == "Domain_researcher"),
        "n_project_cards": len(project_cards),
        "n_duplicate_emails": len(dup_email_set),
    }
    df_stats = pd.DataFrame(list(stats.items()), columns=["metric", "value"])

    # Small codebooks (for transparency / reproducibility)
    codebook_challenges = pd.DataFrame([
        {"code": "no_ai_contacts", "description": "No one to consult / no connections to AI researchers"},
        {"code": "fit_unclear", "description": "Cannot judge if project fits challenge funding"},
        {"code": "data_usability_unclear", "description": "Have data but unsure if usable for AI"},
        {"code": "dont_know_start", "description": "Don't know where to start / what AI can do"},
        {"code": "preprocessing_hard", "description": "Data organization/preprocessing seems hard"},
        {"code": "eval_in_field_unclear", "description": "Unsure how AI use will be evaluated in own field"},
        {"code": "ethics_concerns", "description": "Concerns about ethics, rights, secondary use"},
        {"code": "no_local_talent", "description": "No interested students/personnel in lab"},
        {"code": "ai_applicability_unclear", "description": "Unsure whether AI can be applied to own research"},
        {"code": "no_idea", "description": "Hard to come up with AI-incorporating ideas"},
        {"code": "compute_limits", "description": "Compute/hardware limitations (free-text)"},
        {"code": "publication_constraints", "description": "Journal/publication constraints (free-text)"},
        {"code": "budget", "description": "Budget/cost concerns (free-text)"},
        {"code": "other_free_text", "description": "Other free-text challenges captured verbatim"},
    ])

    codebook_complexity = pd.DataFrame([
        {"code": "time_series", "description": "Time-series data"},
        {"code": "multivariate", "description": "Multivariate"},
        {"code": "linear_nonlinear", "description": "Linear / Nonlinear"},
        {"code": "multimodal", "description": "Multimodal"},
        {"code": "hierarchical", "description": "Hierarchical structure"},
        {"code": "high_dimensional", "description": "High dimensionality"},
    ])

    codebook_ai_categories = pd.DataFrame([
        {"code": "machine_learning", "description": "Machine Learning"},
        {"code": "language_media", "description": "Language and Media Processing"},
        {"code": "agents", "description": "Agents"},
        {"code": "knowledge_sharing", "description": "Knowledge Utilization and Sharing"},
        {"code": "human_interface", "description": "Human Interfaces"},
        {"code": "ai_society", "description": "AI and Society"},
        {"code": "ai_applications", "description": "AI Applications"},
        {"code": "web_intelligence", "description": "Web Intelligence"},
        {"code": "medical_ai", "description": "AI in Medical Technology"},
        {"code": "foundations_theory", "description": "Foundations and Theory"},
        {"code": "robotics_realworld", "description": "Robotics and Real-World Interaction"},
        {"code": "systems_accelerator_chip", "description": "Systems/accelerator chip design"},
        {"code": "image_video_generation", "description": "Image/video generation"},
        {"code": "other_raw", "description": "Other categories kept verbatim"},
    ])

    with pd.ExcelWriter(out_xlsx, engine="openpyxl") as writer:
        df_cards_out.to_excel(writer, index=False, sheet_name="cards_json")
        df_projects_out.to_excel(writer, index=False, sheet_name="project_cards")
        df_stats.to_excel(writer, index=False, sheet_name="stats")
        codebook_challenges.to_excel(writer, index=False, sheet_name="codebook_challenges")
        codebook_complexity.to_excel(writer, index=False, sheet_name="codebook_complexity")
        codebook_ai_categories.to_excel(writer, index=False, sheet_name="codebook_ai_categories")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate structured cards from survey Excel.")
    parser.add_argument("--input", required=True, help="Path to input .xlsx")
    parser.add_argument("--out-jsonl", required=True, help="Output JSONL path")
    parser.add_argument("--out-xlsx", required=True, help="Output XLSX path")
    args = parser.parse_args()

    stats = process_survey_excel(args.input, args.out_jsonl, args.out_xlsx)
    print("Done.")
    for k, v in stats.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()