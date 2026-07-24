"""商品内容の見える化（メール探索・Contact Intelligence・営業画面の共通コンテキスト）。

「何の商品を調査しているのか」を各画面で必ず示せるよう、既存データだけから

    商品名 / 日本語の商品概要 / 主な特徴3点 / source_site / campaign_url /
    official_site_url / 日本クラファン適性スコア / 適性判定理由 / メール探索を実行した理由

を 1 か所で組み立てる。新しいスコア体系は作らず、既存の
``sales_assessment_service``（makuake_fit = 日本クラファン適性）を再利用する。

日本語概要が無い場合は、既存の AI 企業リサーチ結果（company_researches.product_summary）
→ 案件本文（日本語のとき）→ ルールベースの日本語要約 の順で解決する。ルールベースは
決定的・ネットワーク非依存で、キーワード表は ``discovery_scoring_service`` と共用する
（同じ語彙で判定し、画面ごとに違う説明が出ないようにする）。
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

from app.models.project import Project
from app.services import campaign_url as campaign_url_mod
from app.services.discovery_scoring_service import (
    _CAUTION_KEYWORDS,
    _HIGH_FIT_KEYWORDS,
    _match_categories,
)

logger = logging.getLogger("product_context")

# 日本語概要として認める最短長（これ未満は「商品内容が判別できない」扱い）。
MIN_SUMMARY_LEN = 20

# 高評価カテゴリ（英語 canonical）→ 日本語ラベル。概要・特徴の生成に使う。
CATEGORY_LABELS_JA: dict[str, str] = {
    "small gadget": "小型ガジェット",
    "kitchen": "キッチン用品",
    "storage": "収納用品",
    "outdoor": "アウトドア用品",
    "pet": "ペット用品",
    "stationery": "文具",
    "sleep": "睡眠グッズ",
    "relaxation": "リラックス/ウェルネス用品",
    "sustainable": "サステナブル商品",
    "home goods": "生活雑貨",
    "travel": "トラベル用品",
    "design goods": "デザイン雑貨",
}

# 要注意カテゴリ（英語 canonical）→ 日本語ラベル。注意点の提示に使う。
CAUTION_LABELS_JA: dict[str, str] = {
    "medical": "医療・治療領域",
    "supplement": "サプリメント",
    "food": "食品・飲料",
    "cosmetics": "化粧品",
    "wireless": "無線機能（技適）",
    "radio": "電波法対象",
    "large battery": "大型バッテリー（PSE）",
    "children": "子供向け",
    "knife": "刃物",
    "weapon": "武器類",
    "chemical": "化学薬品",
    "alcohol": "酒類",
    "nicotine": "ニコチン製品",
}

# 日本語（かな・漢字）が含まれるか判定する。
_JA_CHARS = re.compile(r"[ぁ-んァ-ヶ一-龥]")


def _text_of(project: Project) -> str:
    return " ".join(
        str(x or "")
        for x in (
            project.title,
            project.description_clean or project.description,
            project.category,
        )
    ).lower()


def _is_japanese(text: str | None) -> bool:
    """日本語（かな）を十分に含むか。中国語・韓国語の本文を誤判定しないよう、かなを見る。"""
    if not text:
        return False
    kana = re.findall(r"[ぁ-んァ-ヶ]", text)
    return len(kana) >= 5


def _funding_phrase(project: Project) -> str | None:
    """達成率・支援者数の日本語フレーズ（実績が無ければ None）。"""
    try:
        goal = float(project.goal_amount or 0)
        raised = float(project.raised_amount or 0)
    except (TypeError, ValueError):
        goal = raised = 0.0
    backers = project.backers_count or 0
    bits: list[str] = []
    if goal > 0 and raised > 0:
        bits.append(f"目標比{int(raised / goal * 100):,}%")
    if backers:
        bits.append(f"支援者{backers:,}人")
    return "・".join(bits) or None


def _site_label(project: Project) -> str:
    from app.services.workflow_service import SITE_LABELS_JA

    site = str(project.source_site or "")
    return SITE_LABELS_JA.get(site, site or "海外クラファン")


def build_japanese_summary(project: Project, *, research_summary: str | None = None) -> str | None:
    """日本語の商品概要（1〜3文）を返す。生成できなければ None。

    優先順:
      1. AI 企業リサーチの product_summary（日本語のとき）
      2. 案件本文 description_clean / description（日本語のとき）
      3. ルールベースの日本語要約（カテゴリ・実績から決定的に生成）
    """
    for candidate in (research_summary, project.description_clean, project.description):
        if _is_japanese(candidate) and len((candidate or "").strip()) >= MIN_SUMMARY_LEN:
            return _trim(str(candidate).strip(), 300)

    # --- ルールベース生成（ネットワーク・API 不要・決定的） ---
    title = (project.title or "").strip()
    if not title:
        return None
    cats = [
        CATEGORY_LABELS_JA[c]
        for c in _match_categories(_text_of(project), _HIGH_FIT_KEYWORDS)
        if c in CATEGORY_LABELS_JA
    ]
    maker = (project.maker_name or "").strip()
    site = _site_label(project)

    kind = "・".join(cats[:2]) if cats else (project.category or "一般消費者向け商品")
    first = f"{site}で公開された{kind}「{title}」です。"
    if maker:
        first = f"{site}で{maker}が公開した{kind}「{title}」です。"

    sentences = [first]
    money = _funding_phrase(project)
    if money:
        sentences.append(f"本国クラウドファンディングでの実績は{money}です。")
    cautions = [
        CAUTION_LABELS_JA[c]
        for c in _match_categories(_text_of(project), _CAUTION_KEYWORDS)
        if c in CAUTION_LABELS_JA
    ]
    if cautions:
        sentences.append(f"輸入・販売時は{('・'.join(cautions[:2]))}の確認が必要です。")

    summary = "".join(sentences)
    return summary if len(summary) >= MIN_SUMMARY_LEN else None


def build_key_features(
    project: Project, *, research_features: list[str] | None = None
) -> list[str]:
    """主な特徴 3 点。AI リサーチの結果があればそれを優先し、無ければ既存データから作る。"""
    if research_features:
        feats = [str(f).strip() for f in research_features if str(f or "").strip()]
        if feats:
            return feats[:3]

    out: list[str] = []
    cats = [
        CATEGORY_LABELS_JA[c]
        for c in _match_categories(_text_of(project), _HIGH_FIT_KEYWORDS)
        if c in CATEGORY_LABELS_JA
    ]
    if cats:
        out.append(f"カテゴリ: {'・'.join(cats[:3])}")
    money = _funding_phrase(project)
    if money:
        out.append(f"クラファン実績: {money}")
    if project.category:
        out.append(f"掲載カテゴリ: {project.category}")
    if project.video_url:
        out.append("紹介動画あり（訴求素材が揃っている）")
    if project.maker_name:
        out.append(f"メーカー: {project.maker_name}")
    out.append(f"取得元: {_site_label(project)}")
    return out[:3]


def _trim(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + "…"


def _latest_research(db: Session, project_id: int):
    try:
        from app.services import company_research_service

        return company_research_service.get_latest_completed(db, project_id)
    except Exception as exc:  # noqa: BLE001  リサーチ未実施でも続行
        logger.info("company research lookup skipped (project=%s): %s", project_id, exc)
        return None


def build(db: Session, project: Project, *, gate: dict | None = None) -> dict:
    """商品コンテキスト（要件 B の表示項目一式）を返す。

    ``gate`` を渡すと日本クラファン適性ゲートの結果を再計算せずに使う
    （ゲート判定と表示で二重計算しないため）。
    """
    research = _latest_research(db, project.id)
    summary = build_japanese_summary(
        project, research_summary=getattr(research, "product_summary", None)
    )
    features = build_key_features(
        project, research_features=getattr(research, "key_product_features", None)
    )
    urls = campaign_url_mod.url_state(project)

    if gate is None:
        from app.services import contact_search_gate

        gate = contact_search_gate.evaluate(db, project, persist=False)

    return {
        "project_id": project.id,
        "product_name": project.title,
        "summary_ja": summary,
        "summary_missing": summary is None,
        "key_features": features,
        "source_site": project.source_site,
        **urls,
        "japan_crowdfunding_score": gate.get("japan_crowdfunding_score"),
        "eligible_for_contact_search": gate.get("eligible_for_contact_search"),
        "contact_search_gate_reason": gate.get("contact_search_gate_reason"),
        "gate_reasons": gate.get("reasons", []),
        "contact_search_rationale": gate.get("rationale"),
    }
