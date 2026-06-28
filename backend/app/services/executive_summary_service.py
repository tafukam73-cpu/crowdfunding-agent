"""AI Executive Summary（営業価値の一目要約）の業務ロジック。

案件詳細を開いた瞬間に「営業すべきか」を判断できるよう、既存の AI 出力を統合して
0〜100 のスコア・星評価・営業対象（YES/NO/要確認）・推奨アクション・推奨チャネル・
理由・注意点を都度算出する。MVP では DB に保存せず、API で算出するだけ。

統合する情報源：
- AI 評価（projects.latest_score / latest_recommendation）
- 日本販売状況チェック（japan_sales_service）
- Contact Intelligence / 連絡先探索（contact_discovery_service）
- 企業リサーチ（company_research_service）
- 日本成功事例との類似（japanese_success_service）
- Ulule 専用スコア（app.ai.ulule）
- sales_status / is_sales_target_candidate / カテゴリ など案件フィールド

synthesize() は DB 非依存の純粋関数として切り出し、スコアリングを単体テストできる
ようにしている。build_summary() が DB から材料を集めて synthesize() を呼ぶ。
"""
from __future__ import annotations

import logging

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app.ai import ulule
from app.models.japanese_success import JapaneseSuccessProject
from app.models.project import Project, SalesStatus
from app.services import (
    company_research_service,
    contact_discovery_service,
    japan_sales_service,
)

logger = logging.getLogger("executive_summary")

# 推奨チャネルとして返す値（要件の許可セット）
ALLOWED_CHANNELS = (
    "email",
    "contact_form",
    "instagram",
    "linkedin",
    "facebook",
    "manual_search",
)

# 連絡先探索の recommended_channel → Executive Summary の許可セットへの正規化
_CHANNEL_NORMALIZE = {
    "email": "email",
    "contact_form": "contact_form",
    "instagram": "instagram",
    "linkedin": "linkedin",
    "facebook": "facebook",
    "press": "manual_search",
    "distributor_page": "manual_search",
    "manual_research": "manual_search",
}

# 既に営業着手済み（今日の優先度を下げる）とみなす営業状況
_ENGAGED_STATUSES = (
    SalesStatus.contacted.value,
    SalesStatus.awaiting_reply.value,
    SalesStatus.replied.value,
    SalesStatus.negotiating.value,
    SalesStatus.won.value,
    SalesStatus.rejected.value,
)
_CLOSED_STATUSES = (SalesStatus.won.value, SalesStatus.rejected.value)
_READY_STATUSES = (SalesStatus.not_started.value, SalesStatus.ready.value)


def _clamp(v: float, lo: int = 0, hi: int = 100) -> int:
    return max(lo, min(hi, int(round(v))))


def _stars_for(score: int) -> int:
    if score >= 85:
        return 5
    if score >= 65:
        return 4
    if score >= 45:
        return 3
    if score >= 25:
        return 2
    return 1


def _avg(vals: list[int]) -> float | None:
    nums = [v for v in vals if v is not None]
    return sum(nums) / len(nums) if nums else None


def _market_fit(sig: dict) -> tuple[str, float | None]:
    """日本市場との相性（テキストレベルと内部スコア）を返す。"""
    parts: list[float] = []
    if sig.get("latest_score") is not None:
        parts.append(sig["latest_score"] * 0.4)  # 最大 40
    if sig.get("similarity_top") is not None:
        parts.append(min(30.0, sig["similarity_top"] * 0.3))
    if sig.get("is_ulule") and sig.get("ulule_jp_lifestyle") is not None:
        parts.append(sig["ulule_jp_lifestyle"] * 0.2)  # 最大 20
    if sig.get("research_japan_fit"):
        parts.append(10.0)
    if not parts:
        return "要確認", None
    fit = sum(parts)
    if fit >= 60:
        return "高い", fit
    if fit >= 38:
        return "中程度", fit
    if fit >= 18:
        return "低い", fit
    return "要確認", fit


def _contact_status_text(sig: dict) -> tuple[str, list[str]]:
    """連絡先取得状況のテキストと、見つかった手段ラベルを返す。"""
    found: list[str] = []
    if sig.get("has_email"):
        found.append("公式メール")
    if sig.get("has_form"):
        found.append("問い合わせフォーム")
    if sig.get("has_instagram"):
        found.append("Instagram")
    if sig.get("has_linkedin"):
        found.append("LinkedIn")
    if sig.get("has_facebook"):
        found.append("Facebook")
    if not sig.get("contact_checked"):
        return "未取得（連絡先探索が必要）", found
    if not found:
        return "有効な連絡手段は未発見", found
    return "・".join(found) + "あり", found


def _japan_status_text(sig: dict) -> str:
    if not sig.get("japan_checked"):
        return "未確認（チェック未実行）"
    if sig.get("has_distributor"):
        return "日本展開あり（代理店または法人）"
    if sig.get("sold_in_japan"):
        return "日本で販売されている"
    if sig.get("no_japan_presence"):
        return "未販売の可能性が高い"
    return "一部のみ確認"


def _recommended_channel(sig: dict, found: list[str]) -> str:
    rc = sig.get("contact_recommended_channel")
    if rc:
        norm = _CHANNEL_NORMALIZE.get(rc)
        if norm in ALLOWED_CHANNELS:
            return norm
    if sig.get("has_email"):
        return "email"
    if sig.get("has_form"):
        return "contact_form"
    if sig.get("has_instagram"):
        return "instagram"
    if sig.get("has_linkedin"):
        return "linkedin"
    if sig.get("has_facebook"):
        return "facebook"
    return "manual_search"


def _score(sig: dict) -> int:
    """営業価値スコア（0〜100）を決定的に算出する。"""
    score = 50.0

    # AI 評価（強いシグナル）：±25
    if sig.get("latest_score") is not None:
        score += (sig["latest_score"] - 50) * 0.5

    # 日本販売状況（高重み）
    if sig.get("japan_checked"):
        if sig.get("has_distributor"):
            score -= 25
        elif sig.get("sold_in_japan"):
            score -= 15
        elif sig.get("no_japan_presence"):
            score += 18

    # 連絡先（Contact Intelligence）
    if sig.get("contact_checked"):
        if sig.get("has_email"):
            score += 12
        if sig.get("has_form"):
            score += 8
        if sig.get("has_instagram") or sig.get("has_linkedin"):
            score += 6
        if sig.get("contactability_score") is not None:
            score += (sig["contactability_score"] - 50) * 0.1  # ±5
    else:
        score -= 5

    # 企業リサーチ
    if sig.get("research_japan_fit"):
        score += 4

    # 日本成功事例との類似
    st = sig.get("similarity_top")
    if st is not None:
        if st >= 60:
            score += 8
        elif st >= 30:
            score += 4

    # Ulule 専用シグナル
    if sig.get("is_ulule"):
        if not sig.get("is_sales_target_candidate"):
            score -= 35
        else:
            avg = _avg(
                [
                    sig.get("ulule_europe_design"),
                    sig.get("ulule_sustainability"),
                    sig.get("ulule_gift"),
                ]
            )
            if avg is not None:
                score += (avg - 50) * 0.2  # ±10
    elif not sig.get("is_sales_target_candidate"):
        # Ulule 以外で営業対象外（通常は発生しない安全網）
        score -= 30

    # 営業状況
    status = sig.get("sales_status")
    if status in _CLOSED_STATUSES:
        score -= 45
    elif status == SalesStatus.negotiating.value:
        score -= 25
    elif status in (
        SalesStatus.awaiting_reply.value,
        SalesStatus.replied.value,
    ):
        score -= 22
    elif status == SalesStatus.contacted.value:
        score -= 15
    elif status in _READY_STATUSES:
        score += 5

    return _clamp(score)


def _sales_target(sig: dict, score: int) -> str:
    disqualified = (not sig.get("is_sales_target_candidate")) or sig.get(
        "has_distributor"
    )
    if disqualified:
        return "no"
    if sig.get("sold_in_japan") and score < 45:
        return "no"
    if score >= 60:
        return "yes"
    if score <= 30:
        return "no"
    return "要確認"


def _recommended_action(sig: dict, score: int, sales_target: str) -> str:
    if (not sig.get("is_sales_target_candidate")) or sig.get("has_distributor"):
        return "営業対象外の可能性"
    if sig.get("sales_status") in _ENGAGED_STATUSES:
        return "後回し"
    if not sig.get("japan_checked"):
        return "日本販売状況を確認"
    has_contact = any(
        sig.get(k)
        for k in ("has_email", "has_form", "has_instagram", "has_linkedin", "has_facebook")
    )
    if not has_contact:
        return "連絡先探索が必要"
    if sales_target == "no":
        return "営業対象外の可能性"
    return "今すぐ営業"


def _reasons(sig: dict, contact_text: str, fit_label: str) -> list[str]:
    out: list[str] = []
    if sig.get("japan_checked") and sig.get("no_japan_presence"):
        out.append("日本販売状況チェックで未販売の可能性が高い")
    if sig.get("japan_checked") and sig.get("has_distributor") is False and (
        sig.get("has_email")
        or sig.get("has_form")
        or sig.get("has_instagram")
        or sig.get("has_linkedin")
    ):
        out.append(f"連絡先が見つかっている（{contact_text}）")
    if sig.get("latest_score") is not None and sig["latest_score"] >= 70:
        out.append(f"AI評価が高い（{sig['latest_score']}/100）")
    st = sig.get("similarity_top")
    if st is not None and st >= 50:
        out.append("日本の成功事例と類似している")
    if sig.get("is_ulule"):
        if (sig.get("ulule_sustainability") or 0) >= 60:
            out.append("Ululeのサステナブル商品で日本のMakuake向き")
        elif (sig.get("ulule_europe_design") or 0) >= 60:
            out.append("ヨーロッパらしいデザイン性がある")
        if (sig.get("ulule_gift") or 0) >= 60 and len(out) < 5:
            out.append("ギフト需要が見込める")
    if fit_label in ("高い", "中程度") and len(out) < 5:
        out.append(f"日本市場との相性が{fit_label}")
    if sig.get("sales_status") in _READY_STATUSES and len(out) < 5:
        out.append("まだ営業未実施")
    # 3 件未満なら一般的な補足で底上げ
    if len(out) < 3 and sig.get("is_sales_target_candidate"):
        out.append("物販商品として営業検討の余地がある")
    return out[:5]


def _cautions(sig: dict) -> list[str]:
    out: list[str] = []
    if not sig.get("is_sales_target_candidate"):
        out.append("営業対象外（非商品）の可能性がある")
    if sig.get("has_distributor"):
        out.append("日本に既存代理店・法人がある可能性")
    if sig.get("sold_in_japan"):
        out.append("日本で既に販売されている可能性")
    if not sig.get("japan_checked"):
        out.append("日本販売状況チェックが未実行")
    if sig.get("contact_checked") and not sig.get("has_email"):
        out.append("メーカー公式メールは未取得")
    if not sig.get("contact_checked"):
        out.append("連絡先探索が未実行")
    if sig.get("sales_status") in _ENGAGED_STATUSES:
        out.append("既に営業着手済み（重複注意）")
    if not out:
        out.append("日本での法規制（PSE等）確認は未実施")
    return out[:3]


def synthesize(sig: dict) -> dict:
    """正規化済みシグナルから Executive Summary を組み立てる（DB 非依存）。"""
    score = _score(sig)
    stars = _stars_for(score)
    fit_label, _fit_score = _market_fit(sig)
    contact_text, found = _contact_status_text(sig)
    sales_target = _sales_target(sig, score)
    return {
        "score": score,
        "stars": stars,
        "sales_target": sales_target,
        "recommended_action": _recommended_action(sig, score, sales_target),
        "recommended_channel": _recommended_channel(sig, found),
        "product_category": sig.get("category") or "不明",
        "japan_sales_status": _japan_status_text(sig),
        "japan_distributor_status": (
            "未確認"
            if not sig.get("japan_checked")
            else ("代理店あり" if sig.get("has_distributor") else "代理店なし")
        ),
        "contact_status": contact_text,
        "japan_market_fit": fit_label,
        "reasons": _reasons(sig, contact_text, fit_label),
        "cautions": _cautions(sig),
    }


def _has_similar_category(db: Session, project: Project) -> bool:
    """同カテゴリの日本成功事例が存在するかを軽量に判定する（EXISTS・全件走査しない）。

    Executive Summary は画面表示で都度呼ばれるため、全成功事例をロードして Python で
    スコアリングする find_similar は使わず、安価な EXISTS だけで類似シグナルを得る。
    詳細な類似事例は「類似する日本の成功事例」パネル（実行/表示時）で確認する。
    """
    cat = (project.category or "").strip()
    if not cat:
        return False
    return bool(
        db.scalar(select(exists().where(JapaneseSuccessProject.category == cat)))
    )


def _gather_signals(db: Session, project: Project) -> dict:
    """DB から各情報源を集め、synthesize() 用の正規化シグナルにする。

    すべて保存済みデータの軽量読み取り（最新行 or EXISTS）で構成し、重い再計算や
    外部アクセスは行わない。
    """
    # 日本販売状況
    js_ctx = japan_sales_service.to_email_context(
        japan_sales_service.get_latest_completed(db, project.id)
    )
    japan_checked = js_ctx is not None

    # 連絡先探索（Contact Intelligence）
    cd = contact_discovery_service.get_latest(db, project.id)
    contact_checked = cd is not None

    # 企業リサーチ
    cr = company_research_service.get_latest_completed(db, project.id)

    # 日本成功事例との類似（軽量な EXISTS のみ。同カテゴリがあれば類似度高とみなす）
    similarity_top = 70 if _has_similar_category(db, project) else None

    # Ulule 専用スコア（product_assessment は 1 回だけ算出して使い回す）
    is_ulule = ulule.is_ulule(project)
    u_axis: dict = {}
    is_candidate = True
    u_sales_target: int | None = None
    if is_ulule:
        try:
            u_axis = ulule.ulule_axis_scores(project)
            pa = ulule.product_assessment(project)
            is_candidate = pa["is_sales_target_candidate"]
            u_sales_target = pa["sales_target_score"]
        except Exception:  # noqa: BLE001  シグナル算出失敗は無視（要約は継続）
            u_axis = {}

    return {
        "latest_score": project.latest_score,
        "recommendation": project.latest_recommendation,
        "japan_checked": japan_checked,
        "no_japan_presence": bool(js_ctx and js_ctx.get("no_japan_presence")),
        "has_distributor": bool(js_ctx and js_ctx.get("has_distributor")),
        "sold_in_japan": bool(js_ctx and js_ctx.get("sold_in_japan")),
        "japan_stars": js_ctx.get("stars") if js_ctx else None,
        "contact_checked": contact_checked,
        "has_email": bool(cd and cd.primary_email),
        "has_form": bool(cd and cd.primary_contact_form_url),
        "has_instagram": bool(cd and cd.instagram_url),
        "has_linkedin": bool(cd and cd.linkedin_url),
        "has_facebook": bool(cd and cd.facebook_url),
        "contactability_score": cd.contactability_score if cd else None,
        "contact_recommended_channel": cd.recommended_channel if cd else None,
        "research_japan_fit": (cr.japan_market_fit or "") if cr else "",
        "similarity_top": similarity_top,
        "is_ulule": is_ulule,
        "is_sales_target_candidate": is_candidate,
        "ulule_europe_design": u_axis.get("europe_design_score"),
        "ulule_sustainability": u_axis.get("sustainability_score"),
        "ulule_gift": u_axis.get("gift_potential_score"),
        "ulule_jp_lifestyle": u_axis.get("japan_lifestyle_fit_score"),
        "ulule_sales_target_score": u_sales_target,
        "sales_status": project.sales_status,
        "category": project.category,
    }


def build_summary(db: Session, project: Project) -> dict:
    """案件の Executive Summary を算出して返す（DB 保存はしない）。"""
    sig = _gather_signals(db, project)
    result = synthesize(sig)
    result["project_id"] = project.id
    return result
