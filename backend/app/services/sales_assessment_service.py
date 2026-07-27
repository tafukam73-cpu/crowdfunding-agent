"""営業適性アセスメント（Sales Copilot v2 の基盤・ルールベース）。

営業対象案件（projects）に対し 3 つの適性スコアを **決定的・再計算可能** に算出する:

  - japan_market_fit_score : 日本市場適性（カテゴリ受容性 × 実績 × 規制の軽さ）
  - exclusivity_score      : 独占販売可能性（日本未上陸度 × メーカー規模 × 到達性）
  - makuake_fit_score      : Makuake 適性（物販/新規性 × 実績 × 日本新規性）

方針:
- まずルールベース（Claude 非依存・決定的）。純粋関数 assess() を DB から分離してテスト。
- 既存の保存データだけで算出（推測しない）。入力が乏しければ confidence を下げる。
- 任意で AI 補正（ai_adjuster）を後段に重ねられる（ハイブリッド）。既定は rule-based のみ。
- 非破壊: 実行のたびに sales_assessments に 1 行追加し、最新を採用する。

入力シグナル（_gather_signals）:
  project（カテゴリ/資金/支援者/メーカー名/公式サイト有無）＋ japan_sales_check（日本
  販売状況・★）＋ contact_discovery（到達性）から作る。
"""
from __future__ import annotations

import logging

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.models.project import Project
from app.services.category_keywords import (
    CAUTION_KEYWORDS as _CAUTION_KEYWORDS,
    HIGH_FIT_KEYWORDS as _HIGH_FIT_KEYWORDS,
    LOGISTICS_HEAVY as _LOGISTICS_HEAVY,
    clamp as _clamp,
    match_categories as _match_categories,
)

logger = logging.getLogger("sales_assessment")

RULE_ENGINE_NAME = "rule-based-sales-assessment-v1"

# 非物販（体験/サービス/寄付/計画）を示す語。Makuake 適性を大きく下げる。
_NON_PRODUCT_HINTS = (
    "計畫", "計画", "出征", "世界盃", "課程", "工作坊", "養成", "療癒", "諮詢",
    "訂閱", "沙龍", "體驗", "体験", "講座", "募款", "捐款", "公益",
    "service", "course", "workshop", "coaching", "membership", "subscription",
    "charity", "donation", "fund our", "campaign to", "nonprofit", "ngo",
    "retreat", "consulting", "plan ",
)

# 大企業/グローバルブランド。独占販売権を新規輸入業者へ渡す見込みが低い。
_MEGA_BRAND_HINTS = (
    "lg", "sony", "samsung", "panasonic", "philips", "bosch", "xiaomi", "anker",
    "asus", "acer", "lenovo", "dell", "microsoft", "google", "apple", "huawei",
    "tcl", "haier", "nintendo", "canon", "nikon", "bose", "jbl", "logitech",
    "dyson", "3m", "coca-cola", "nestle", "unilever", "procter",
)

# 中小/独立系メーカーを示す語（独占販売交渉に応じやすい）。
_SME_HINTS = (
    "工作室", "studio", "工房", "個人", "獨立", "独立", "lab", "labo", "atelier",
    "手作", "craft", "maker", "workshop team",
)

# 日本クラファンで受けやすい物販カテゴリ（Makuake 適性の加点）。
_MAKUAKE_FAVORED = (
    "small gadget", "kitchen", "storage", "outdoor", "sleep", "relaxation",
    "home goods", "travel", "design goods", "stationery", "pet", "sustainable",
)


# ---------------- 入力シグナル（純粋な dict） ----------------
def _text_of(sig: dict) -> str:
    return " ".join(
        str(sig.get(k) or "") for k in ("title", "description", "category")
    ).lower()


def _achievement_rate(sig: dict) -> int | None:
    """達成率（%）。rate_pct があればそれ、無ければ raised/goal から算出。"""
    if sig.get("rate_pct") is not None:
        try:
            return int(sig["rate_pct"])
        except (TypeError, ValueError):
            return None
    raised, goal = sig.get("raised"), sig.get("goal")
    try:
        if raised is not None and goal and float(goal) > 0:
            return int(float(raised) / float(goal) * 100)
    except (TypeError, ValueError):
        return None
    return None


def _traction_score(sig: dict) -> int:
    """クラファン実績（達成率＋支援者数）→ 0〜100。実績が無ければ中立寄り。"""
    rate = _achievement_rate(sig)
    backers = sig.get("backers")
    if rate is None and not backers:
        return 45  # 実績不明は中立やや下
    score = 40
    if rate is not None:
        # 100% で +15、以降 100% ごとに +5（上限 +45 / 1000%程度で頭打ち）
        score += 15 if rate >= 100 else int(rate / 100 * 15)
        if rate > 100:
            score += min(30, (rate - 100) // 100 * 5)
    if backers:
        try:
            b = int(backers)
            score += min(15, b // 100)  # 支援者 100 人ごとに +1、上限 +15
        except (TypeError, ValueError):
            pass
    return _clamp(score)


def _regulatory_ease(text: str) -> tuple[int, list[str]]:
    """規制・輸入の軽さ（高い=安全）→ 0〜100 と理由。"""
    caution = _match_categories(text, _CAUTION_KEYWORDS)
    score = 90
    reasons: list[str] = []
    if caution:
        score -= 18 * len(caution)
        reasons.append(f"規制/輸入リスク: {', '.join(caution)}")
    heavy = [c for c in caution if c in _LOGISTICS_HEAVY]
    if heavy:
        score -= 10
        reasons.append(f"技適/PSE等の重い物流: {', '.join(heavy)}")
    if not caution:
        reasons.append("規制上の重いカテゴリに該当なし")
    return _clamp(score), reasons


# ---------------- 3 スコア（純粋関数） ----------------
def _level(score: int) -> str:
    return "high" if score >= 67 else ("mid" if score >= 40 else "low")


def score_japan_market_fit(sig: dict) -> dict:
    """日本市場適性: カテゴリ受容性 × クラファン実績 × 規制の軽さ。"""
    text = _text_of(sig)
    high = _match_categories(text, _HIGH_FIT_KEYWORDS)
    caution = _match_categories(text, _CAUTION_KEYWORDS)
    reasons: list[str] = []

    category = 52 + 12 * len(high) - 12 * len(caution)
    category = _clamp(category)
    if high:
        reasons.append(f"日本で受けやすいカテゴリ: {', '.join(high)}")
    if caution:
        reasons.append(f"要注意カテゴリ: {', '.join(caution)}")
    if not high and not caution:
        reasons.append("カテゴリの明確な加点/減点なし")

    traction = _traction_score(sig)
    if traction >= 60:
        reasons.append("本国クラファンで強い実績（需要の裏付け）")
    reg, reg_reasons = _regulatory_ease(text)
    reasons += reg_reasons

    score = _clamp(0.40 * category + 0.35 * traction + 0.25 * reg)
    return {
        "score": score, "level": _level(score), "reasons": reasons,
        "subscores": {"category_appeal": category, "traction": traction,
                      "regulatory_ease": reg},
    }


def score_exclusivity(sig: dict) -> dict:
    """独占販売可能性: 日本未上陸度 × メーカー規模 × 到達性。"""
    reasons: list[str] = []

    # 日本未上陸度。根拠のある結果だけを強く反映する（検索ゼロ＝未販売と断定しない）。
    jr = sig.get("japan_result")          # sold_in_japan / not_found_in_japan / inconclusive
    jconf = int(sig.get("japan_confidence") or 0)
    stars = sig.get("japan_stars")
    if jr is not None:
        if jr == "sold_in_japan":
            japan_openness = 18
            reasons.append("日本で販売/代理店を確認 → 独占余地が小さい（根拠あり）")
        elif jr == "not_found_in_japan":
            # 「検索で見つからない」は不在の確証ではないため、確度で頭打ちにする。
            japan_openness = _clamp(52 + jconf * 0.35)  # conf 55→71, 80→80
            reasons.append(
                f"日本での販売が確認されず → 独占の余地あり（確度 {jconf}・断定はしない）"
            )
        else:  # inconclusive
            japan_openness = 50
            reasons.append("日本販売状況は不確定（inconclusive）→ 中立扱い")
    elif sig.get("already_in_japan"):
        japan_openness = 20
        reasons.append("日本で既に販売/代理店あり → 独占余地が小さい")
    elif stars is not None:
        japan_openness = _clamp(20 + int(stars) * 16)  # ★5→100, ★1→36
        if int(stars) >= 4:
            reasons.append("日本未上陸の可能性が高い → 独占販売の好機")
    else:
        japan_openness = 48
        reasons.append("日本販売状況チェック未実施 → 未上陸度は不明（中立・要確認）")

    # メーカー規模（大企業→独占付与されにくい / 中小・独立→応じやすい）
    maker = (sig.get("maker_name") or "").lower()
    if any(b == maker or f" {b} " in f" {maker} " or maker.startswith(b + " ")
           or maker == b for b in _MEGA_BRAND_HINTS):
        maker_size = 18
        reasons.append("大手/グローバルブランド → 独占販売権は取りにくい")
    elif any(h in maker for h in _SME_HINTS) or (maker and len(maker) <= 24):
        maker_size = 82
        reasons.append("中小/独立系メーカー → 独占交渉に応じやすい")
    else:
        maker_size = 55

    # 到達性（連絡先が取れているほど交渉に入りやすい）
    if sig.get("has_contact"):
        reach = 85
        reasons.append("連絡先を取得済み → 交渉に入りやすい")
    elif sig.get("has_official_site"):
        reach = 60
    else:
        reach = 30
        reasons.append("連絡先/公式サイト未取得 → まず到達手段の確保が必要")

    score = _clamp(0.40 * japan_openness + 0.35 * maker_size + 0.25 * reach)
    return {
        "score": score, "level": _level(score), "reasons": reasons,
        "subscores": {"japan_openness": japan_openness, "maker_size": maker_size,
                      "reachability": reach},
    }


def score_makuake_fit(sig: dict) -> dict:
    """Makuake 適性: 物販/新規性 × 実績 × 日本新規性。"""
    text = _text_of(sig)
    reasons: list[str] = []

    # 商品タイプ（物販・新規ガジェット/デザイン→高 / 体験・サービス・寄付→低）
    favored = [c for c in _match_categories(text, _HIGH_FIT_KEYWORDS)
               if c in _MAKUAKE_FAVORED]
    non_product = any(h in text for h in _NON_PRODUCT_HINTS)
    product_type = 50 + 12 * len(favored)
    if non_product:
        product_type -= 35
        reasons.append("体験/サービス/計画型 → Makuake の物販モデルに不向き")
    if favored:
        reasons.append(f"Makuake で人気の物販カテゴリ: {', '.join(favored)}")
    product_type = _clamp(product_type)

    # 本国クラファン実績（Makuake は実績ある製品を好む）
    traction = _traction_score(sig)
    if traction >= 60:
        reasons.append("本国クラファン実績あり → Makuake でも訴求しやすい")

    # 日本新規性（Makuake/GreenFunding 未掲載・日本未販売ほど高い）
    if sig.get("on_makuake"):
        novelty = 15
        reasons.append("既に Makuake/GREEN FUNDING 掲載 → 新規性が低い")
    elif sig.get("already_in_japan"):
        novelty = 35
        reasons.append("日本で既販売 → 『日本初上陸』の訴求が弱い")
    else:
        stars = sig.get("japan_stars")
        novelty = _clamp(45 + int(stars) * 10) if stars is not None else 60

    score = _clamp(0.40 * product_type + 0.30 * traction + 0.30 * novelty)
    return {
        "score": score, "level": _level(score), "reasons": reasons,
        "subscores": {"product_type": product_type, "traction": traction,
                      "japan_novelty": novelty},
    }


# ---------------- 総合・信頼度 ----------------
def _confidence(sig: dict) -> int:
    """入力の充足度（0〜100）。資金/実績・日本販売状況・説明文の有無で決める。

    日本販売チェックの状態で加点を段階化する（根拠が強いほど加点大）。未実施/実行中/
    失敗/inconclusive はスコアを極端に下げず、confidence のみ下げる。
    """
    score = 30
    if _achievement_rate(sig) is not None or sig.get("backers"):
        score += 25
    # 日本販売状況：completed かつ確度が高いほど加点。未確定は小さめ。
    jstate = sig.get("japan_state")
    jr = sig.get("japan_result")
    jconf = int(sig.get("japan_confidence") or 0)
    if jstate == "completed" and jr in ("sold_in_japan", "not_found_in_japan"):
        score += _clamp_add(10 + int(jconf * 0.2), 25)  # 最大 +25
    elif jr == "inconclusive":
        score += 5
    elif jstate is None and sig.get("japan_checked"):
        score += 25  # 後方互換（japan_state 未指定の呼び出し）
    # not_checked / queued / running / failed は加点なし（confidence が下がる）
    if len((sig.get("description") or "")) >= 40:
        score += 15
    if sig.get("maker_name"):
        score += 5
    return _clamp(score)


def _clamp_add(value: int, cap: int) -> int:
    return max(0, min(cap, int(value)))


def assess(sig: dict) -> dict:
    """3 スコア＋総合優先度＋信頼度を算出（DB 非依存の純粋関数）。"""
    jm = score_japan_market_fit(sig)
    ex = score_exclusivity(sig)
    mk = score_makuake_fit(sig)
    # 総合優先度: 独占余地と日本市場適性を重視し、Makuake 適性で補強。
    overall = _clamp(0.40 * ex["score"] + 0.35 * jm["score"] + 0.25 * mk["score"])
    return {
        "japan_market_fit": jm,
        "exclusivity": ex,
        "makuake_fit": mk,
        "overall_priority_score": overall,
        "confidence": _confidence(sig),
        "engine": RULE_ENGINE_NAME,
    }


# ---------------- DB 連携 ----------------
def _latest_japan_sales_check(db: Session, project_id: int):
    from app.models.japan_sales_check import JapanSalesCheck, JapanSalesStatus

    stmt = (
        select(JapanSalesCheck)
        .where(
            JapanSalesCheck.project_id == project_id,
            JapanSalesCheck.status == JapanSalesStatus.completed.value,
        )
        .order_by(desc(JapanSalesCheck.created_at), desc(JapanSalesCheck.id))
        .limit(1)
    )
    return db.scalar(stmt)


def _latest_contact_discovery(db: Session, project_id: int):
    from app.services import contact_discovery_service as cds

    return cds.get_latest(db, project_id)


# --- 日本販売状況の解釈（根拠のある結果だけを使う） ---
# 「売っている確証」があるチャネル（見つかったら sold_in_japan）。
_JAPAN_PRESENCE_KEYS = ("distributor", "subsidiary", "amazon", "rakuten", "yahoo")
_JAPAN_CF_KEYS = ("makuake", "greenfunding")

# 案件別のスコア用グレード基準（要件）。
_GRADE_BANDS = ((80, "A"), (65, "B"), (50, "C"), (35, "D"), (0, "E"))


def grade(score: int | None) -> str | None:
    """0〜100 のスコアを A〜E に変換する（None は None）。"""
    if score is None:
        return None
    s = _clamp(score)
    for lo, g in _GRADE_BANDS:
        if s >= lo:
            return g
    return "E"


def interpret_japan_check(jsc, *, job_status: str | None = None) -> dict:
    """最新の japan_sales_check（＋任意のジョブ状態）を根拠付きで解釈する。

    検索結果がゼロ＝「日本未販売」と断定しない。実際に販売を確認できたら
    sold_in_japan、能動的に検索して見つからなければ not_found_in_japan（確度は中）、
    判定材料が乏しければ inconclusive。

    Returns:
      {status, result, evidence[], source_urls[], checked_at, confidence,
       error_reason, version}
      status: not_checked / queued / running / completed / failed
      result: sold_in_japan / not_found_in_japan / inconclusive / None
    """
    from app.models.japan_sales_check import JapanSalesStatus

    # ジョブが進行中なら、まだ結果は無い（暫定）。
    if job_status in ("queued", "running"):
        return {
            "status": job_status, "result": None, "evidence": [], "source_urls": [],
            "checked_at": None, "confidence": 0, "error_reason": None, "version": None,
        }
    if jsc is None:
        return {
            "status": "not_checked", "result": None, "evidence": [],
            "source_urls": [], "checked_at": None, "confidence": 0,
            "error_reason": None, "version": None,
        }

    checked_at = jsc.created_at.isoformat() if getattr(jsc, "created_at", None) else None
    version = getattr(jsc, "model", None)

    if jsc.status == JapanSalesStatus.failed.value:
        return {
            "status": "failed", "result": None, "evidence": [], "source_urls": [],
            "checked_at": checked_at, "confidence": 0,
            "error_reason": (jsc.error or "check failed")[:300], "version": version,
        }
    if jsc.status != JapanSalesStatus.completed.value:
        # pending 等（ジョブ経由でない直呼び）→ 未確定扱い
        return {
            "status": "running", "result": None, "evidence": [], "source_urls": [],
            "checked_at": checked_at, "confidence": 0, "error_reason": None,
            "version": version,
        }

    channels = jsc.channels or []
    found, not_found, unknown = [], [], []
    evidence: list[str] = []
    source_urls: list[str] = []
    for ch in channels:
        st = str(ch.get("status", "")).lower()
        key = str(ch.get("channel", "")).lower()
        label = ch.get("label") or key
        url = ch.get("search_url")
        if st in ("found", "limited"):
            found.append(key)
            evidence.append(f"{label}: {st}" + (f"（{ch.get('note')}）" if ch.get("note") else ""))
            if url:
                source_urls.append(url)
        elif st == "not_found":
            not_found.append(key)
            if url:
                source_urls.append(url)
        else:
            unknown.append(key)

    presence = any(k in found for k in _JAPAN_PRESENCE_KEYS)
    on_cf = any(k in found for k in _JAPAN_CF_KEYS)
    checked_keys = len(found) + len(not_found)

    if presence or on_cf:
        result = "sold_in_japan"
        confidence = 85 if presence else 70
    elif checked_keys >= 3 and not found:
        # 主要チャネルを能動的に検索して販売を確認できなかった → 未販売の可能性（確定はしない）
        result = "not_found_in_japan"
        # 実検索できたチャネル比率で確度を作る（最大 65。断定しない）
        total = max(1, len(channels))
        confidence = _clamp(40 + int(checked_keys / total * 30))
        confidence = min(confidence, 65)
        evidence.append(f"{checked_keys} チャネルを検索し販売を確認できず（不在の確証ではない）")
    else:
        # 検索材料が乏しい（unknown 多数）→ 不確定
        result = "inconclusive"
        confidence = 25
        evidence.append("判定できるチャネル情報が不足（inconclusive）")

    return {
        "status": "completed", "result": result, "evidence": evidence[:8],
        "source_urls": sorted(set(source_urls))[:8], "checked_at": checked_at,
        "confidence": confidence, "error_reason": None, "version": version,
    }


def missing_data(sig: dict) -> list[str]:
    """confidence を下げている不足データを列挙する（画面の『データ不足』表示用）。"""
    miss: list[str] = []
    if _achievement_rate(sig) is None and not sig.get("backers"):
        miss.append("funding_traction")
    js = sig.get("japan_state")
    if js in ("not_checked", "queued", "running", "failed") or sig.get("japan_result") == "inconclusive":
        miss.append("japan_sales_check")
    if not sig.get("has_contact") and not sig.get("has_official_site"):
        miss.append("contact")
    if len((sig.get("description") or "")) < 40:
        miss.append("description")
    return miss


def _gather_signals(db: Session, project: Project, *, japan_job_status: str | None = None) -> dict:
    """project ＋ 最新の日本販売状況 ＋ 連絡先探索から入力シグナルを作る。"""
    jsc = _latest_japan_sales_check(db, project.id)
    disc = _latest_contact_discovery(db, project.id)

    japan = interpret_japan_check(jsc, job_status=japan_job_status)
    already_in_japan = japan["result"] == "sold_in_japan"
    on_makuake = False
    stars = jsc.sales_value_stars if jsc is not None else None
    if jsc is not None:
        for ch in (jsc.channels or []):
            st = str(ch.get("status", "")).lower()
            key = str(ch.get("channel", "")).lower()
            if st in ("found", "limited") and ("makuake" in key or "green" in key):
                on_makuake = True

    has_contact = False
    has_official = False
    if disc is not None:
        has_contact = bool(
            disc.primary_email or disc.primary_contact_form_url
            or disc.discovered_emails or disc.discovered_forms
        )
        from app.services.contact_discovery_service import official_site_or_none

        has_official = bool(official_site_or_none(disc.official_site_url))
    if not has_official:
        from app.services.contact_discovery_service import official_site_or_none

        has_official = bool(official_site_or_none(project.maker_url))

    rate_pct = None
    return {
        "title": project.title,
        "description": project.description_clean or project.description,
        "category": project.category,
        "raised": float(project.raised_amount) if project.raised_amount is not None else None,
        "goal": float(project.goal_amount) if project.goal_amount is not None else None,
        "rate_pct": rate_pct,
        "backers": project.backers_count,
        "maker_name": project.maker_name,
        "has_official_site": has_official,
        "has_contact": has_contact,
        "japan_checked": japan["status"] == "completed",
        "japan_stars": stars,
        "already_in_japan": already_in_japan,
        "on_makuake": on_makuake,
        "source_site": project.source_site,
        # 日本販売状況の解釈（根拠つき）
        "japan_state": japan["status"],
        "japan_result": japan["result"],
        "japan_confidence": japan["confidence"],
        "japan_evidence": japan["evidence"],
        "japan_source_urls": japan["source_urls"],
        "japan_checked_at": japan["checked_at"],
        "japan_error_reason": japan["error_reason"],
        "japan_version": japan["version"],
    }


def run_assessment(
    db: Session, project: Project, *, ai_adjuster=None, japan_job_status: str | None = None
):
    """アセスメントを実行して sales_assessments に保存する（非破壊・履歴追加）。

    japan_job_status を渡すと、日本販売チェックが queued/running の暫定評価として
    記録できる（confidence が下がる）。ai_adjuster を渡すとルールベース結果に AI
    補正を重ねる（既定は rule-based のみ）。失敗してもルールベース結果は保存する。
    """
    from app.models.sales_assessment import SalesAssessment

    sig = _gather_signals(db, project, japan_job_status=japan_job_status)
    result = assess(sig)
    ai_adjusted = False
    if ai_adjuster is not None:
        try:
            adjusted = ai_adjuster(sig, result)
            if isinstance(adjusted, dict):
                result = adjusted
                ai_adjusted = True
        except Exception as exc:  # noqa: BLE001  AI 補正失敗はルールベースにフォールバック
            logger.warning("sales assessment AI adjuster failed (project=%s): %s",
                           project.id, exc)

    row = SalesAssessment(
        project_id=project.id,
        japan_market_fit_score=result["japan_market_fit"]["score"],
        exclusivity_score=result["exclusivity"]["score"],
        makuake_fit_score=result["makuake_fit"]["score"],
        overall_priority_score=result["overall_priority_score"],
        details_json={
            "japan_market_fit": result["japan_market_fit"],
            "exclusivity": result["exclusivity"],
            "makuake_fit": result["makuake_fit"],
            "missing_data": missing_data(sig),
            "provisional": japan_job_status in ("queued", "running"),
            "signals": sig,
        },
        confidence=result["confidence"],
        engine=result.get("engine", RULE_ENGINE_NAME),
        ai_adjusted=ai_adjusted,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_japan_check(db: Session, project: Project, *, runner=None):
    """日本販売状況が未実施/失敗なら背景ジョブを作成する（重複ジョブは作らない）。

    Returns: (job|None, state)。state は not_checked/queued/running/completed/failed。
    - 既に completed → ジョブ作成せず (None, "completed")。
    - queued/running のジョブが既にある → 既存を返す（重複作成しない）。
    - not_checked/failed → 新規ジョブを作成。
    """
    from app.models.contact_intelligence_job import CIJobType
    from app.services import contact_intelligence_service as ci

    active = ci.find_active(db, project.id, CIJobType.japan_sales_check.value)
    if active is not None:
        return active, active.status  # queued / running（重複作成しない）

    jsc = _latest_japan_sales_check(db, project.id)
    japan = interpret_japan_check(jsc)
    if japan["status"] in ("not_checked", "failed"):
        job, _from_cache = ci.create_job(
            db, project, CIJobType.japan_sales_check.value, force=True, runner=runner
        )
        return job, job.status
    return None, japan["status"]


def assess_with_japan(db: Session, project: Project, *, auto_check: bool = True, runner=None) -> dict:
    """営業適性アセスメントを実行。日本販売チェックが未実施なら背景ジョブを起動し、
    現在取得可能なデータで暫定アセスメントを保存する（非破壊）。

    ジョブ完了後は job 側で再計算され、新しい行として保存される（履歴保持）。

    Returns: {assessment, japan_job_id, japan_job_status, japan_state, provisional}
    """
    job = None
    japan_state = None
    if auto_check:
        job, japan_state = ensure_japan_check(db, project, runner=runner)
    running = job is not None and job.status in ("queued", "running")
    row = run_assessment(
        db, project, japan_job_status=(job.status if running else None)
    )
    return {
        "assessment": row,
        "japan_job_id": job.id if job is not None else None,
        "japan_job_status": job.status if job is not None else None,
        "japan_state": japan_state,
        "provisional": running,
    }


def list_unevaluated(db: Session, *, site: str | None = None, limit: int | None = None):
    """営業対象で sales_assessment がまだ無い案件を返す（未評価バッチ対象）。"""
    from sqlalchemy import exists, select

    from app.models.project import (
        SALES_TARGET_SITES,
        Project,
        not_archived_clause,
    )
    from app.models.sales_assessment import SalesAssessment

    values = [s.value for s in SALES_TARGET_SITES]
    has_assessment = exists().where(SalesAssessment.project_id == Project.id)
    stmt = select(Project).where(
        Project.source_site.in_(values), not_archived_clause(), ~has_assessment
    )
    if site:
        stmt = stmt.where(Project.source_site == site)
    stmt = stmt.order_by(Project.id)
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt))


def run_missing_assessments(
    db: Session, *, site: str | None = None, limit: int | None = None, runner=None
) -> dict:
    """未評価の営業対象案件を明示的に一括評価する（GET からは呼ばない）。

    各案件で assess_with_japan を実行：現在のデータで暫定 Assessment を保存し、
    日本販売チェックが未実施なら背景ジョブを起動する（重複ジョブは作らない）。
    連絡先が無くても日本市場適性/Makuake 適性は評価でき、総合を 0 点にはしない。
    """
    projects = list_unevaluated(db, site=site, limit=limit)
    results = []
    for p in projects:
        try:
            out = assess_with_japan(db, p, auto_check=True, runner=runner)
            results.append({
                "project_id": p.id,
                "overall": out["assessment"].overall_priority_score,
                "confidence": out["assessment"].confidence,
                "provisional": out["provisional"],
                "japan_job_status": out["japan_job_status"],
            })
        except Exception as exc:  # noqa: BLE001  1 件失敗で全体を止めない
            logger.warning("run_missing_assessment failed (project=%s): %s", p.id, exc)
            db.rollback()
            results.append({"project_id": p.id, "error": str(exc)[:200]})
    return {
        "requested_site": site,
        "unevaluated_found": len(projects),
        "evaluated": sum(1 for r in results if "overall" in r),
        "results": results,
    }


def get_latest(db: Session, project_id: int):
    from app.models.sales_assessment import SalesAssessment

    stmt = (
        select(SalesAssessment)
        .where(SalesAssessment.project_id == project_id)
        .order_by(desc(SalesAssessment.created_at), desc(SalesAssessment.id))
        .limit(1)
    )
    return db.scalar(stmt)
