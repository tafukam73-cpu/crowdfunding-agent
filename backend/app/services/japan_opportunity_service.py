"""日本市場機会 分析の業務ロジック（Japan Opportunity Engine v1-2）。

発掘商品（discovered_products）に対する日本市場評価の分析結果を保存・取得する
土台。v1-2 では CRUD のみで、ルールベース評価・AI 評価・実検索は後続で実装する。

- スコアは 0〜100 に正規化（範囲外は丸め、非数値は None）。
- evidence_json は dict / list のみ安全に保存（それ以外は None）。
- 1 発掘商品に複数分析を持てる（最新を取得できる）。
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.discovered_product import DiscoveredProduct
from app.models.japan_opportunity_analysis import JapanOpportunityAnalysis
from app.schemas.japan_opportunity import SCORE_FIELDS
from app.services.discovery_scoring_service import (
    _CAUTION_KEYWORDS,
    _HIGH_FIT_KEYWORDS,
    _LOGISTICS_HEAVY,
    _SHORT_DESC_THRESHOLD,
    _match_categories,
    _parse_ai_json,
)

logger = logging.getLogger("japan_opportunity")

_VALID_SORT = {"score_desc", "created_desc"}

RULE_ENGINE_NAME = "rule-based-japan-opportunity-v1"

# 総合スコアの重み（docs/japan_opportunity_engine_strategy.md §3.10・合計 1.0）
_OVERALL_WEIGHTS: dict[str, float] = {
    "japan_market_fit_score": 0.20,
    "japan_entry_gap_score": 0.15,
    "crowdfunding_fit_score": 0.12,
    "retail_fit_score": 0.10,
    "regulatory_safety_score": 0.12,
    "logistics_score": 0.08,
    "margin_potential_score": 0.10,
    "competition_gap_score": 0.08,
    "sales_success_score": 0.05,
}


def _clamp_score(value: Any) -> int | None:
    """0〜100 の整数に正規化する。None はそのまま、非数値は None（安全側）。"""
    if value is None:
        return None
    try:
        v = int(round(float(value)))
    except (TypeError, ValueError):
        return None
    return max(0, min(100, v))


def _safe_evidence(value: Any) -> Any:
    """evidence_json は dict / list のみ受け入れる（それ以外は None）。"""
    if isinstance(value, (dict, list)):
        return value
    return None


def _normalize(data: dict) -> dict:
    """入力 dict のスコアを正規化し、evidence_json を安全化した新しい dict を返す。"""
    out = dict(data)
    for field in SCORE_FIELDS:
        if field in out:
            out[field] = _clamp_score(out[field])
    if "evidence_json" in out:
        out["evidence_json"] = _safe_evidence(out["evidence_json"])
    return out


def create_analysis(
    db: Session, discovered_product_id: int, data: dict
) -> JapanOpportunityAnalysis:
    """分析を作成する。発掘商品が存在しなければ ValueError。

    data には discovered_product_id を含めない（引数で受け取る）。含まれていても
    引数の値を優先する。
    """
    product = db.get(DiscoveredProduct, discovered_product_id)
    if product is None:
        raise ValueError(f"発掘商品が見つかりません: id={discovered_product_id}")

    payload = _normalize(data)
    payload.pop("discovered_product_id", None)
    analysis = JapanOpportunityAnalysis(
        discovered_product_id=discovered_product_id, **payload
    )
    db.add(analysis)
    db.commit()
    db.refresh(analysis)
    logger.info(
        "japan opportunity analysis created: id=%s product=%s score=%s",
        analysis.id, discovered_product_id, analysis.overall_opportunity_score,
    )
    return analysis


def get_analysis(db: Session, analysis_id: int) -> JapanOpportunityAnalysis | None:
    return db.get(JapanOpportunityAnalysis, analysis_id)


def get_latest_for_product(
    db: Session, discovered_product_id: int
) -> JapanOpportunityAnalysis | None:
    """発掘商品の最新分析（作成日時が新しいもの）を返す。無ければ None。"""
    stmt = (
        select(JapanOpportunityAnalysis)
        .where(
            JapanOpportunityAnalysis.discovered_product_id == discovered_product_id
        )
        .order_by(
            desc(JapanOpportunityAnalysis.created_at),
            desc(JapanOpportunityAnalysis.id),
        )
        .limit(1)
    )
    return db.scalar(stmt)


def update_analysis(
    db: Session, analysis_id: int, updates: dict
) -> JapanOpportunityAnalysis | None:
    """分析を更新する（渡されたフィールドのみ）。対象が無ければ None。

    discovered_product_id は変更しない（渡されても無視）。
    """
    analysis = db.get(JapanOpportunityAnalysis, analysis_id)
    if analysis is None:
        return None

    payload = _normalize(updates)
    payload.pop("discovered_product_id", None)
    for key, value in payload.items():
        setattr(analysis, key, value)
    db.commit()
    db.refresh(analysis)
    return analysis


def list_analyses(
    db: Session,
    *,
    discovered_product_id: int | None = None,
    min_score: int | None = None,
    sort: str = "score_desc",
) -> list[JapanOpportunityAnalysis]:
    """分析一覧を取得する（フィルター・並び替え対応）。

    sort:
      - "score_desc"（既定）: 総合機会スコア降順（未設定は末尾）
      - "created_desc"      : 作成日時の新しい順
    """
    stmt = select(JapanOpportunityAnalysis)
    if discovered_product_id is not None:
        stmt = stmt.where(
            JapanOpportunityAnalysis.discovered_product_id == discovered_product_id
        )
    if min_score is not None:
        stmt = stmt.where(
            JapanOpportunityAnalysis.overall_opportunity_score >= min_score
        )

    if sort == "created_desc":
        stmt = stmt.order_by(
            desc(JapanOpportunityAnalysis.created_at),
            desc(JapanOpportunityAnalysis.id),
        )
    else:  # "score_desc"（既定）。未設定（NULL）は末尾へ（DB 非依存の並び）。
        stmt = stmt.order_by(
            JapanOpportunityAnalysis.overall_opportunity_score.is_(None),
            desc(JapanOpportunityAnalysis.overall_opportunity_score),
            desc(JapanOpportunityAnalysis.id),
        )
    return list(db.scalars(stmt).all())


# --------------------------------------------------------------------------- #
# ルールベース評価（Japan Opportunity Engine v1-3）
# --------------------------------------------------------------------------- #
def _to_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None


def _clamp(value: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(value))))


def _or_rule(discovery_value: Any, rule_value: float) -> int:
    """Discovery 側スコアがあればそれを、無ければルール値を採用する（0〜100）。"""
    dv = _to_int(discovery_value)
    return _clamp(dv if dv is not None else rule_value)


def _funding_backers_boost(funding: float | None, backers: int | None) -> int:
    """調達額・支援者数が大きいほど加点する共通ブースト（CF/営業に効く）。"""
    boost = 0
    if backers is not None:
        if backers >= 5000:
            boost += 15
        elif backers >= 1000:
            boost += 8
        elif backers >= 100:
            boost += 3
    if funding is not None:
        if funding >= 500_000:
            boost += 10
        elif funding >= 100_000:
            boost += 5
    return boost


def analyze_product_rules(
    db: Session, discovered_product_id: int
) -> JapanOpportunityAnalysis:
    """発掘商品をルールベースで評価し、分析結果を作成して返す。

    商品名/カテゴリ/説明/status/funding/backers/country と、あれば Discovery 側の
    スコアを使って 0〜100 の各軸を算出する。不明情報は 0 点ではなく中立点＋低
    confidence とし、failed / canceled / ended でも除外しない。AI・実検索は使わない
    （実ネットワークなし）。発掘商品が無ければ ValueError。
    """
    product = db.get(DiscoveredProduct, discovered_product_id)
    if product is None:
        raise ValueError(f"発掘商品が見つかりません: id={discovered_product_id}")
    data = _compute_rule_data(product)
    return create_analysis(db, discovered_product_id, data)


def _compute_rule_data(product: DiscoveredProduct) -> dict:
    """発掘商品からルールベースの分析データ（dict）を計算する（DB 非依存）。

    analyze_product_rules（保存）と analyze_product_ai（AI 評価のベースライン）で
    共有する。スコア軸・総合・confidence・根拠テキスト・evidence_json を含む。
    """
    # --- 入力の整理 ---
    text = " ".join(
        str(getattr(product, k) or "")
        for k in ("category", "product_name", "project_title", "description")
    ).lower()
    high_hits = _match_categories(text, _HIGH_FIT_KEYWORDS)
    caution_hits = _match_categories(text, _CAUTION_KEYWORDS)
    logistics_heavy = [c for c in caution_hits if c in _LOGISTICS_HEAVY]

    funding = _to_float(product.funding_amount)
    backers = _to_int(product.backers_count)
    fb_boost = _funding_backers_boost(funding, backers)

    desc = str(product.description or "").strip()
    short_desc = len(desc) < _SHORT_DESC_THRESHOLD

    # Discovery 側スコア（あれば活用）
    d_japan_fit = product.japan_fit_score
    d_cf = product.crowdfunding_fit_score
    d_logistics = product.logistics_score
    d_reg = product.regulatory_risk_score
    d_comp = product.competition_risk_score
    d_entry = product.japan_entry_risk_score

    # --- 各軸（0〜100・高いほど有利） ---
    # 日本市場適合度：高評価カテゴリで加点、要注意カテゴリで小減点
    jm_rule = 50 + (20 + 5 * min(len(high_hits), 3) if high_hits else 0) \
        - (10 if caution_hits else 0)
    japan_market_fit = _or_rule(d_japan_fit, jm_rule)

    # クラファン適性：Discovery 値（or ルール）＋ funding/backers ブースト
    cf_base = _or_rule(d_cf, 50 + (15 if high_hits else 0))
    crowdfunding_fit = _clamp(cf_base + fb_boost)

    # 一般販売適性：日用品・実用寄りは加点、要注意は小減点
    retail_fit = _clamp(
        50 + (10 + 3 * min(len(high_hits), 2) if high_hits else 0)
        - (5 if caution_hits else 0)
    )

    # 規制リスクの低さ：要注意カテゴリで大きく下げる（高い=安全）
    reg_rule = 75
    if caution_hits:
        reg_rule -= 30 + 10 * min(len(caution_hits), 3)
    regulatory_safety = _or_rule(d_reg, reg_rule)

    # 輸送しやすさ：小型・軽量で加点、技適/PSE/大型電池等で減点
    log_rule = 55 + (10 if high_hits else 0) - (20 if logistics_heavy else 0)
    logistics = _or_rule(d_logistics, log_rule)

    # 競合リスクの低さ：Discovery 値 or 中立（未検索のため中立寄り）
    competition_gap = _or_rule(d_comp, 55)

    # 日本未進出可能性：Discovery 値 or 中立（実未上陸判定は v1-5 で確定）
    japan_entry_gap = _or_rule(d_entry, 60)

    # 利益率見込み：仕入/売価が未確定のため中立。市場性（funding）で微加点
    margin_potential = _clamp(50 + (5 if (funding or 0) >= 100_000 else 0))

    # 営業成功可能性：連絡先探索未実行のため中立＋実績（funding/backers）で加点
    sales_success = _clamp(50 + fb_boost)

    axes = {
        "japan_market_fit_score": japan_market_fit,
        "japan_entry_gap_score": japan_entry_gap,
        "crowdfunding_fit_score": crowdfunding_fit,
        "retail_fit_score": retail_fit,
        "regulatory_safety_score": regulatory_safety,
        "logistics_score": logistics,
        "margin_potential_score": margin_potential,
        "competition_gap_score": competition_gap,
        "sales_success_score": sales_success,
    }
    overall = sum(axes[k] * w for k, w in _OVERALL_WEIGHTS.items())
    overall_opportunity = _clamp(overall)

    # --- confidence（情報の充実度。不明が多いほど低い） ---
    has_discovery = any(
        v is not None
        for v in (d_japan_fit, d_cf, d_logistics, d_reg, d_comp, d_entry)
    )
    confidence = 50
    if has_discovery:
        confidence += 15
    confidence += 10 if not short_desc else -15
    if funding is not None or backers is not None:
        confidence += 10
    if product.category:
        confidence += 5
    confidence_score = _clamp(confidence)

    # --- 根拠テキスト・evidence ---
    summaries = _build_summaries(
        product=product,
        high_hits=high_hits,
        caution_hits=caution_hits,
        logistics_heavy=logistics_heavy,
        funding=funding,
        backers=backers,
        short_desc=short_desc,
        overall=overall_opportunity,
    )
    evidence = {
        "engine": RULE_ENGINE_NAME,
        "product_category_signals": {
            "high_fit_categories": high_hits,
            "matched_high_fit": bool(high_hits),
        },
        "risk_signals": {
            "caution_categories": caution_hits,
            "logistics_heavy_categories": logistics_heavy,
        },
        "funding_signals": {
            "funding_amount": funding,
            "backers_count": backers,
            "status": product.status,
        },
        "discovery_scores_used": {
            "japan_fit_score": d_japan_fit,
            "crowdfunding_fit_score": d_cf,
            "logistics_score": d_logistics,
            "regulatory_risk_score": d_reg,
            "competition_risk_score": d_comp,
            "japan_entry_risk_score": d_entry,
            "overall_discovery_score": product.overall_discovery_score,
        },
        "confidence_factors": {
            "has_discovery_scores": has_discovery,
            "description_length": len(desc),
            "short_description": short_desc,
            "has_funding_info": funding is not None or backers is not None,
            "has_category": bool(product.category),
        },
    }

    return {**axes,
            "overall_opportunity_score": overall_opportunity,
            "confidence_score": confidence_score,
            **summaries,
            "evidence_json": evidence}


def _build_summaries(
    *,
    product: DiscoveredProduct,
    high_hits: list[str],
    caution_hits: list[str],
    logistics_heavy: list[str],
    funding: float | None,
    backers: int | None,
    short_desc: bool,
    overall: int,
) -> dict:
    """根拠テキスト（各 summary / reasoning / strategy / next_action）を組み立てる。"""
    # 日本進出状況（v1-3 では実検索しないため未確定と明記）
    japan_presence = (
        "日本進出状況はルールベース（v1-3）では未確認です。実際の未上陸判定"
        "（Amazon.co.jp / 楽天 / Makuake 等の検索）は今後のバージョンで確定します。"
    )
    if product.country:
        japan_presence += f" 発掘元の国: {product.country}。"

    competition = (
        "国内競合はルールベースでは実検索していないため中立に評価しました。"
        if not high_hits
        else "日用品・実用カテゴリのため一定の需要が見込めますが、"
             "競合状況は実検索で要確認です。"
    )

    if caution_hits:
        regulatory = (
            "規制・輸入で確認が必要なカテゴリ（"
            + " / ".join(caution_hits)
            + "）を含みます。該当は販売不可を意味しませんが、許認可・輸入要件の"
            "確認が必要です（断定はしません）。"
        )
    else:
        regulatory = "明確な規制カテゴリは検出されませんでしたが、最終的な確認は必要です。"

    if logistics_heavy:
        logistics_s = (
            "技適 / PSE / 大型バッテリー等に該当する可能性があり、輸送・認証の"
            "負担が大きい見込みです（" + " / ".join(logistics_heavy) + "）。"
        )
    elif high_hits:
        logistics_s = "小型・軽量が想定され、輸入・国内配送はしやすい見込みです。"
    else:
        logistics_s = "輸送しやすさは中立に評価しました（寸法・重量が不明）。"

    parts = []
    if funding is not None:
        parts.append(f"調達額 {funding:,.0f}")
    if backers is not None:
        parts.append(f"支援者 {backers:,} 人")
    pricing = (
        ("海外CF実績（" + " / ".join(parts) + "）から市場性は一定あります。")
        if parts
        else "海外CFの実績データが乏しく、市場性は判断材料が不足しています。"
    )
    pricing += " 粗利は仕入/売価が未確定のため確度は低めです。"

    reason_bits = []
    if high_hits:
        reason_bits.append(
            "日本市場・物流と相性の良いカテゴリ（" + " / ".join(high_hits) + "）に該当。"
        )
    if caution_hits:
        reason_bits.append(
            "規制・輸入で注意が必要なカテゴリ（" + " / ".join(caution_hits)
            + "）を含み、規制安全度を低めに評価。"
        )
    if not high_hits and not caution_hits:
        reason_bits.append("カテゴリからは明確な適合/リスク要因を検出できず中立評価。")
    if backers is not None and backers >= 1000:
        reason_bits.append(f"支援者数 {backers:,} 人と実績があり CF/営業に加点。")
    if short_desc:
        reason_bits.append("説明文が短く情報不足のため confidence は低め。")
    opportunity_reasoning = " ".join(reason_bits)

    if overall >= 70:
        strategy = (
            "優先度高。Makuake / GREEN FUNDING での日本先行クラファンを前提に、"
            "メーカーへ日本展開・独占販売を打診する。"
        )
        next_action = (
            "Contact Intelligence を開始してメーカーの連絡先を特定し、"
            "日本展開・独占販売の打診メールを準備する。"
        )
    elif overall >= 45:
        strategy = (
            "有望。日本未上陸か・競合・規制を確認したうえでアプローチを検討する。"
        )
        next_action = (
            "日本未上陸判定・競合・規制の追加確認を行い、良好なら Contact "
            "Intelligence を開始する。"
        )
    else:
        strategy = "現時点では優先度低。規制・競合リスクを精査し保留候補とする。"
        next_action = "規制・競合・市場性の追加情報を集め、再評価する。"
    if caution_hits:
        next_action += (
            "（規制対応：" + " / ".join(caution_hits) + " の許認可・輸入要件を要確認）"
        )

    return {
        "japan_presence_summary": japan_presence,
        "competition_summary": competition,
        "regulatory_summary": regulatory,
        "logistics_summary": logistics_s,
        "pricing_summary": pricing,
        "opportunity_reasoning": opportunity_reasoning,
        "recommended_strategy": strategy,
        "recommended_next_action": next_action,
    }


# --------------------------------------------------------------------------- #
# AI 評価連携（Japan Opportunity Engine v1-4）
# --------------------------------------------------------------------------- #
AI_ENGINE_NAME = "ai-japan-opportunity-v1"

# AI が上書き/補完できる根拠テキスト
_AI_TEXT_FIELDS = (
    "japan_presence_summary",
    "competition_summary",
    "regulatory_summary",
    "logistics_summary",
    "pricing_summary",
    "opportunity_reasoning",
    "recommended_strategy",
    "recommended_next_action",
)


def _build_ai_prompt(product: DiscoveredProduct, rule_data: dict) -> str:
    """AI 評価用プロンプトを組み立てる（商品情報＋ルール評価＋注意書き）。"""
    lines = [
        "あなたは海外クラウドファンディング商品の日本展開を評価するアナリストです。",
        "この評価は営業前の予備評価であり、法的・規制上の断定ではありません。",
        "不明な情報は断定しないでください。",
        "日本未進出・競合・法規制は「要確認」として扱ってください。",
        "各スコアは 0〜100 で評価し、高いほど日本展開・営業に有利とします。",
        "推薦理由（opportunity_reasoning）と次アクション（recommended_next_action）"
        "は日本語で書いてください。",
        "出力は JSON のみとし、前後に説明文を付けないでください。",
        "",
        "# 商品情報",
        f"product_name: {product.product_name}",
        f"project_title: {product.project_title}",
        f"category: {product.category}",
        f"country: {product.country}",
        f"status: {product.status}",
        f"funding_amount: {product.funding_amount}",
        f"backers_count: {product.backers_count}",
        f"description: {(product.description or '')[:1500]}",
        "",
        "# ルールベース評価（ベースライン。妥当なら踏襲し、根拠があれば調整する）",
    ]
    for field in SCORE_FIELDS:
        lines.append(f"{field}: {rule_data.get(field)}")
    lines += [
        "",
        "# 出力する JSON のキー（スコアは 0〜100 の整数）",
        ", ".join(SCORE_FIELDS) + ",",
        ", ".join(_AI_TEXT_FIELDS) + ", evidence_json",
    ]
    return "\n".join(lines)


def _coerce_raw(raw: Any) -> Any:
    """ai_raw_response として JSON 保存できる形に整える。"""
    if raw is None or isinstance(raw, (dict, list, str)):
        return raw
    return str(raw)


def _apply_ai(
    product: DiscoveredProduct, rule_data: dict, ai_fn: Any
) -> tuple[dict, bool, str | None, Any]:
    """ルール評価を土台に AI 評価を重ねる。安全にフォールバックする。

    Returns: (最終 data, ai_used, fallback_reason, ai_raw_response)
    """
    rule_baseline = {f: rule_data.get(f) for f in SCORE_FIELDS}
    final = dict(rule_data)
    ai_used = False
    fallback_reason: str | None = None
    ai_raw: Any = None
    parsed: dict | None = None

    if ai_fn is None:
        fallback_reason = "ai_fn_not_provided"
    else:
        prompt = _build_ai_prompt(product, rule_data)
        try:
            raw = ai_fn(prompt)
        except Exception as exc:  # noqa: BLE001  AI 例外はフォールバック
            fallback_reason = f"ai_exception:{exc}"[:300]
            logger.warning("japan opportunity AI failed: %s", exc)
        else:
            ai_raw = _coerce_raw(raw)
            if raw is None or (isinstance(raw, str) and not raw.strip()):
                fallback_reason = "ai_empty"
            else:
                try:
                    parsed = _parse_ai_json(raw)
                    ai_used = True
                except Exception as exc:  # noqa: BLE001  不正 JSON はフォールバック
                    fallback_reason = "ai_invalid_json"
                    logger.warning(
                        "japan opportunity AI returned invalid JSON: %s", exc
                    )

    if ai_used and parsed is not None:
        # スコアは範囲外を 0〜100 に正規化して上書き（非数値は無視＝ルール維持）
        for field in SCORE_FIELDS:
            v = _clamp_score(parsed.get(field))
            if v is not None:
                final[field] = v
        # 根拠テキストは非空のみ上書き
        for field in _AI_TEXT_FIELDS:
            t = parsed.get(field)
            if isinstance(t, str) and t.strip():
                final[field] = t

    # evidence_json：ルール根拠を土台に AI メタ情報を必ず付与
    evidence = dict(rule_data.get("evidence_json") or {})
    if ai_used and parsed is not None and isinstance(
        parsed.get("evidence_json"), (dict, list)
    ):
        evidence["ai_evidence"] = parsed.get("evidence_json")
    evidence["ai_used"] = ai_used
    evidence["fallback_reason"] = fallback_reason
    evidence["rule_baseline"] = rule_baseline
    evidence["ai_raw_response"] = ai_raw
    evidence["engine"] = AI_ENGINE_NAME if ai_used else evidence.get("engine")
    final["evidence_json"] = evidence

    return final, ai_used, fallback_reason, ai_raw


def analyze_product_ai(
    db: Session,
    discovered_product_id: int,
    ai_fn: Any = None,
) -> JapanOpportunityAnalysis:
    """発掘商品を AI 評価（＋ルールベース土台）で分析し、作成して返す。

    - まず v1-3 のルールベース評価をベースラインとして計算する。
    - ai_fn が指定されたときのみ AI 評価を実行し、スコア・根拠を上書き/補完する。
    - AI 応答が壊れている/空/例外/未指定のときは、必ずルールベースへフォールバック
      する（例外は投げない）。スコアは 0〜100 に正規化する。
    - evidence_json に ai_used / fallback_reason / rule_baseline / ai_raw_response を残す。

    ai_fn の契約: ``ai_fn(prompt: str) -> dict | str``（JSON 文字列または dict）。
    発掘商品が無ければ ValueError。
    """
    product = db.get(DiscoveredProduct, discovered_product_id)
    if product is None:
        raise ValueError(f"発掘商品が見つかりません: id={discovered_product_id}")

    rule_data = _compute_rule_data(product)
    final, ai_used, fallback_reason, _raw = _apply_ai(product, rule_data, ai_fn)
    logger.info(
        "japan opportunity AI analysis: product=%s ai_used=%s fallback=%s",
        discovered_product_id, ai_used, fallback_reason,
    )
    return create_analysis(db, discovered_product_id, final)
