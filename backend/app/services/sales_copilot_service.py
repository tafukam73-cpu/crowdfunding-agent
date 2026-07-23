"""営業 AI コパイロットの業務ロジック。

案件一覧・企業リサーチ・Contact Intelligence・CRM・営業状況・返信待ち・フォロー状況を
横断し、「この案件は今どう動くべきか」を **1 つの判断（decision）＋理由（reasons）＋
次の一手（next_action）＋アクション（actions）** に落とし込む。

設計方針（要件 6：まずはルールベース）:
- Claude API には依存しない。既存の保存済みデータだけで判断する。
- 判断ロジック `classify()` は DB 非依存の純粋関数として切り出し、単体テストできる。
- 各案件の要約は既存の Executive Summary（`executive_summary_service`）を再利用し、
  そこに「企業リサーチ済み / メール生成済み / 公式サイト確認済み / 最終営業からの日数」を
  加えて判断する。
- 後で Claude 要約を足せるよう、`build_card()` の `summary` は独立した dict にしている。

decision（判断カテゴリ）:
    sell_now          今すぐ営業すべき
    needs_research    まず企業リサーチが必要
    needs_contact     まず連絡先探索が必要（Contact Intelligence）
    needs_email       営業メール生成が必要
    needs_followup    フォローアップが必要
    waiting           返信待ちで放置（待つ）
    needs_negotiation 商談対応が必要（返信あり／商談中）
    drop              見送り候補
    data_insufficient データ不足（未評価・公式サイト未確認 等）
    closed            対応済み（成約／見送り。営業候補から除外）
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.contact_discovery import ContactDiscovery
from app.models.email_draft import EmailDraft
from app.models.project import SALES_TARGET_SITES, Project, SalesStatus
from app.services import (
    contact_discovery_service,
    company_research_service,
    executive_summary_service as ess,
    workflow_service,
)

_SALES_TARGET_VALUES = [s.value for s in SALES_TARGET_SITES]
_READY = (SalesStatus.not_started.value, SalesStatus.ready.value)
_CLOSED = (SalesStatus.won.value, SalesStatus.rejected.value)

# フォローアップ対象になり得る営業状況（メール送信済み・返信待ち）。
_ENGAGED_WAIT = (SalesStatus.contacted.value, SalesStatus.awaiting_reply.value)

# 「今すぐ営業」に振り分ける AI 総合スコアのしきい値（これ未満で連絡先ありは準備工程へ）。
SELL_NOW_SCORE = 60
# フォロー開始とみなす最終営業からの経過日数。
FOLLOWUP_MIN_DAYS = 3


# --- 判断カテゴリのメタ情報（ラベル・次の一手・アクション・緊急度の重み） ---
DECISIONS: dict[str, dict] = {
    "sell_now": {
        "label": "今すぐ営業すべき",
        "next_action": "営業メールを作成して送付する",
        "actions": ["email", "change_status", "open"],
        "weight": 30,
    },
    "needs_negotiation": {
        "label": "商談対応が必要",
        "next_action": "返信内容を確認して商談を進める",
        "actions": ["email", "change_status", "open"],
        "weight": 26,
    },
    "needs_followup": {
        "label": "フォローアップが必要",
        "next_action": "フォローアップメールを作成する",
        "actions": ["followup", "change_status", "open"],
        "weight": 22,
    },
    "needs_email": {
        "label": "営業メール生成が必要",
        "next_action": "営業メールを生成する",
        "actions": ["email", "company_research", "open"],
        "weight": 14,
    },
    "needs_contact": {
        "label": "まず連絡先探索が必要",
        "next_action": "Contact Intelligence で連絡先を探索する",
        "actions": ["contact_intelligence", "open"],
        "weight": 10,
    },
    "needs_research": {
        "label": "まず企業リサーチが必要",
        "next_action": "企業リサーチでパーソナライズ材料を集める",
        "actions": ["company_research", "email", "open"],
        "weight": 8,
    },
    "waiting": {
        "label": "返信待ちで放置",
        "next_action": "返信を待つ（フォロー期間内）",
        "actions": ["change_status", "open"],
        "weight": 3,
    },
    "data_insufficient": {
        "label": "データ不足",
        "next_action": "公式サイト確認・連絡先探索でデータを補完する",
        "actions": ["contact_intelligence", "company_research", "open"],
        "weight": 2,
    },
    "drop": {
        "label": "見送り候補",
        "next_action": "見送り（ステータスを見送りに）を検討する",
        "actions": ["change_status", "open"],
        "weight": 1,
    },
    "closed": {
        "label": "対応済み（成約／見送り）",
        "next_action": "対応不要",
        "actions": ["open"],
        "weight": 0,
    },
}


def _has_any_contact(sig: dict) -> bool:
    return bool(
        sig.get("has_email")
        or sig.get("has_form")
        or sig.get("has_instagram")
        or sig.get("has_linkedin")
        or sig.get("has_facebook")
    )


def classify(sig: dict) -> dict:
    """正規化済みシグナルから 1 つの判断を下す（DB 非依存の純粋関数）。

    返り値: {decision, decision_label, primary_reason, next_action, actions, urgency}
    `primary_reason` は「なぜそう判断したか」の中心的理由（必ず 1 つ返る＝要件 3）。
    """
    status = sig.get("sales_status")
    score = sig.get("latest_score")
    days = sig.get("days_since_last_outreach")

    decision, reason = _decide(sig, status, score, days)

    meta = DECISIONS[decision]
    # 緊急度：営業価値スコア（無ければ 40）＋ 判断カテゴリの重み ＋ フォロー日数の加点。
    base = score if score is not None else 40
    urgency = base + meta["weight"]
    if decision == "needs_followup" and days is not None:
        urgency += min(20, days)  # 返信が無いほど優先

    return {
        "decision": decision,
        "decision_label": meta["label"],
        "primary_reason": reason,
        "next_action": meta["next_action"],
        "actions": list(meta["actions"]),
        "urgency": int(urgency),
    }


def _decide(sig: dict, status, score, days) -> tuple[str, str]:
    """判断カテゴリと中心的理由を返す（最初に一致した規則を採用）。"""
    # 1. 成約 / 見送りは営業対象から除外
    if status in _CLOSED:
        label = "成約済み" if status == SalesStatus.won.value else "見送り済み"
        return "closed", f"{label}のため営業対象外"

    # 2. 商談フェーズ（返信あり／商談中）は最優先で対応
    if status == SalesStatus.negotiating.value:
        return "needs_negotiation", "商談中のため対応が必要"
    if status == SalesStatus.replied.value:
        return "needs_negotiation", "返信があったため商談対応が必要"

    # 3. 営業済み・返信待ち → 経過日数でフォロー / 待機に分岐
    if status in _ENGAGED_WAIT:
        if days is not None and days >= FOLLOWUP_MIN_DAYS:
            return "needs_followup", f"{days}日返信がないためフォロー推奨"
        d = "" if days is None else f"（営業から{days}日）"
        return "waiting", f"営業済みで返信待ち期間中{d}のため待機"

    # 4. 未営業（未接触 / 準備完了）
    # 4a. 見送り候補：日本に代理店/販売済み
    if sig.get("has_distributor"):
        return "drop", "日本に代理店・法人がある可能性が高い"
    if sig.get("sold_in_japan"):
        return "drop", "日本で既に販売されている可能性が高い"

    # 4b. 連絡先があれば営業アクションへ（連絡先ありは data 不足に落とさない）
    if _has_any_contact(sig):
        if score is not None and score >= SELL_NOW_SCORE:
            return "sell_now", f"連絡先があり営業価値スコアが高い（{score}）ため優先営業"
        if not sig.get("email_done"):
            return "needs_email", "連絡先はあるが営業メールが未生成のため作成が必要"
        if not sig.get("research_done"):
            return "needs_research", "連絡先・メールはあるが企業リサーチが未実施"
        return "sell_now", "連絡先・メール・リサーチが揃っており営業可能"

    # 4c. 連絡先なし → データ不足 か 連絡先探索
    if score is None:
        return "data_insufficient", "AI未評価のため判断材料が不足している"
    if not sig.get("has_official_site"):
        return "data_insufficient", "公式サイト未確認のため営業不可（データ不足）"
    return "needs_contact", "メール等の連絡先が未発見のため Contact Intelligence が必要"


# ----------------------------------------------------------------------------
#  DB からの材料集め（build_card / dashboard）
# ----------------------------------------------------------------------------
def _funding(project: Project) -> dict:
    raised = project.raised_amount
    goal = project.goal_amount
    rate = None
    if raised is not None and goal is not None and float(goal) > 0:
        rate = round(float(raised) / float(goal) * 100)
    return {
        "currency": project.currency,
        "raised_amount": float(raised) if raised is not None else None,
        "goal_amount": float(goal) if goal is not None else None,
        "backers_count": project.backers_count,
        "rate_pct": rate,
    }


def _short(text: str | None, limit: int = 160) -> str | None:
    if not text:
        return None
    t = " ".join(text.split())
    return t if len(t) <= limit else t[: limit - 1] + "…"


def _last_action_text(days: int | None, email_done: bool) -> str:
    if days is None:
        return "まだ営業アクションなし" if not email_done else "営業メール作成済み"
    if days == 0:
        return "本日営業アクションあり"
    return f"最終営業から{days}日経過"


def build_card(
    db: Session,
    project: Project,
    *,
    sig: dict | None = None,
    summary: dict | None = None,
    extras: dict | None = None,
) -> dict:
    """1 案件の営業サマリー＋判断＋アクションをまとめたカードを返す。

    `sig` / `summary` / `extras` を渡さない場合は DB から都度算出する（単一案件用）。
    """
    if sig is None:
        sig = ess._gather_signals(db, project)
    if summary is None:
        summary = ess.synthesize(sig)
    if extras is None:
        extras = _gather_extras(db, project)

    merged = {**sig, **extras}
    decision = classify(merged)

    # 会社概要・商品概要（企業リサーチがあれば優先。無ければ案件情報から）
    cr = extras.get("company_research")
    company = _short(getattr(cr, "brand_summary", None)) or (
        project.maker_name or "会社情報は未リサーチ"
    )
    product = (
        _short(getattr(cr, "product_summary", None))
        or _short(project.description_clean)
        or project.title
    )

    # 理由：中心的理由を先頭に、Executive Summary の理由を続けて重複なく最大 5 件。
    reasons: list[str] = [decision["primary_reason"]]
    for r in summary.get("reasons", []):
        if r not in reasons:
            reasons.append(r)
    reasons = reasons[:5]

    days = extras.get("days_since_last_outreach")
    return {
        "project_id": project.id,
        "title": project.title,
        "source_site": project.source_site,
        "decision": decision["decision"],
        "decision_label": decision["decision_label"],
        "next_action": decision["next_action"],
        "actions": decision["actions"],
        "reasons": reasons,
        "priority_score": summary["score"],
        "stars": summary["stars"],
        "urgency": decision["urgency"],
        "recommended_channel": summary.get("recommended_channel"),
        "recommended_email": summary.get("recommended_email"),
        "summary": {
            "product": product,
            "company": company,
            "japan_market_fit": summary.get("japan_market_fit"),
            "japan_sales_status": summary.get("japan_sales_status"),
            "funding": _funding(project),
            "contact_status": summary.get("contact_status"),
            "contact_person_found": summary.get("contact_person_found", False),
            "contact_person_name": summary.get("contact_person_name"),
            "contact_person_title": summary.get("contact_person_title"),
            "contact_person_department": summary.get("contact_person_department"),
            "sales_status": project.sales_status,
            "last_action": _last_action_text(days, extras.get("email_done", False)),
            "days_since_last_outreach": days,
            "next_action": decision["next_action"],
            "risks": summary.get("cautions", []),
            "recommendation": {
                "score": summary["score"],
                "stars": summary["stars"],
                "sales_target": summary.get("sales_target"),
            },
        },
    }


def _gather_extras(db: Session, project: Project) -> dict:
    """単一案件用：企業リサーチ済み / メール生成済み / 公式サイト / 最終営業日数を集める。"""
    cr = company_research_service.get_latest_completed(db, project.id)
    email_done = bool(
        db.scalar(select(exists().where(EmailDraft.project_id == project.id)))
    )
    cd = contact_discovery_service.get_latest(db, project.id)
    official = (
        contact_discovery_service.official_site_or_none(cd.official_site_url)
        if cd
        else None
    )
    # 実際の営業アクション（SalesActivity / EmailDraft）だけで日数を測る。
    # updated_at へフォールバックすると「未営業なのに本日営業」と誤表示するため使わない。
    last = workflow_service._last_outreach_map(db, [project.id]).get(project.id)
    days = workflow_service._days_since(datetime.now(timezone.utc), last)
    return {
        "company_research": cr,
        "research_done": cr is not None,
        "email_done": email_done,
        "has_official_site": official is not None,
        "days_since_last_outreach": days,
    }


def project_copilot(db: Session, project: Project) -> dict:
    """単一案件のコパイロット・カードを返す。"""
    return build_card(db, project)


# ----------------------------------------------------------------------------
#  ダッシュボード（横断的にバケット分類）
# ----------------------------------------------------------------------------
def _batch_research_ids(db: Session, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    rows = db.scalars(
        select(CompanyResearch.project_id)
        .where(
            CompanyResearch.project_id.in_(ids),
            CompanyResearch.research_status == ResearchStatus.completed.value,
        )
        .distinct()
    )
    return {pid for pid in rows if pid is not None}


def _batch_official_site_ids(db: Session, ids: list[int]) -> set[int]:
    """公式サイト（有効な business URL）が確認済みの project_id 集合。"""
    if not ids:
        return set()
    rows = db.execute(
        select(ContactDiscovery.project_id, ContactDiscovery.official_site_url).where(
            ContactDiscovery.project_id.in_(ids),
            ContactDiscovery.official_site_url.isnot(None),
        )
    ).all()
    out: set[int] = set()
    for pid, url in rows:
        if pid is not None and contact_discovery_service.official_site_or_none(url):
            out.add(pid)
    return out


def copilot_dashboard(db: Session, *, per_bucket: int = 5, scan_limit: int = 200) -> dict:
    """営業 AI コパイロットのダッシュボードを返す。

    営業対象サイトの案件を横断してカード化・判断し、バケットに振り分ける。
    - top_action        : 今日の最重要アクション（緊急度最大の 1 件）
    - priority_sales    : 優先営業案件 TOP5（sell_now）
    - needs_contact     : 連絡先探索すべき案件
    - followup          : フォローすべき案件
    - drop_candidates   : 見送り候補
    - data_insufficient : データ不足案件
    - ai_comment        : AI からのコメント（ルールベースの総括）
    - counts            : 判断カテゴリ別の件数

    パフォーマンスのため走査は scan_limit 件に制限する（AI 総合スコア降順・未評価は末尾）。
    """
    stmt = (
        select(Project)
        .where(Project.source_site.in_(_SALES_TARGET_VALUES))
        .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
        .limit(scan_limit)
    )
    projects = list(db.scalars(stmt))
    ids = [p.id for p in projects]

    # バッチで軽量シグナルを集める（per-project の追加クエリを避ける）。
    research_ids = _batch_research_ids(db, ids)
    email_ids = workflow_service._ids_with_email(db, ids)
    official_ids = _batch_official_site_ids(db, ids)
    outreach = workflow_service._last_outreach_map(db, ids)
    now = datetime.now(timezone.utc)

    cards: list[dict] = []
    for p in projects:
        sig = ess._gather_signals(db, p)
        summary = ess.synthesize(sig)
        # 実際の営業アクションがある場合のみ日数を測る（未営業は None）。
        extras = {
            "company_research": None,  # 表示カード生成時に必要な分だけ後で補完
            "research_done": p.id in research_ids,
            "email_done": p.id in email_ids,
            "has_official_site": p.id in official_ids,
            "days_since_last_outreach": workflow_service._days_since(
                now, outreach.get(p.id)
            ),
        }
        cards.append(build_card(db, p, sig=sig, summary=summary, extras=extras))

    # 判断カテゴリ別に集計
    counts: dict[str, int] = {}
    for c in cards:
        counts[c["decision"]] = counts.get(c["decision"], 0) + 1

    def _bucket(decision: str) -> list[dict]:
        items = [c for c in cards if c["decision"] == decision]
        items.sort(key=lambda c: c["urgency"], reverse=True)
        return items[:per_bucket]

    priority_sales = _bucket("sell_now")
    needs_contact = _bucket("needs_contact")
    followup = _bucket("needs_followup")
    drop_candidates = _bucket("drop")
    data_insufficient = _bucket("data_insufficient")
    needs_email = _bucket("needs_email")

    # 今日の最重要アクション：成約/見送り/待機を除いた中で緊急度最大の 1 件。
    actionable = [
        c for c in cards if c["decision"] not in ("closed", "drop", "waiting")
    ]
    actionable.sort(key=lambda c: c["urgency"], reverse=True)
    top_action = actionable[0] if actionable else None

    ai_comment = _ai_comment(counts, top_action, len(cards))

    return {
        "top_action": top_action,
        "priority_sales": priority_sales,
        "needs_contact": needs_contact,
        "needs_email": needs_email,
        "followup": followup,
        "drop_candidates": drop_candidates,
        "data_insufficient": data_insufficient,
        "counts": counts,
        "ai_comment": ai_comment,
        "scanned": len(cards),
    }


def _ai_comment(counts: dict[str, int], top_action: dict | None, scanned: int) -> str:
    """ルールベースの総括コメント（後で Claude 要約に差し替え可能な構造）。"""
    if scanned == 0:
        return "営業対象の案件がありません。まず案件を収集してください。"
    parts: list[str] = []
    if counts.get("sell_now"):
        parts.append(f"今すぐ営業できる案件が{counts['sell_now']}件")
    if counts.get("needs_followup"):
        parts.append(f"フォローすべき案件が{counts['needs_followup']}件")
    if counts.get("needs_negotiation"):
        parts.append(f"商談対応が必要な案件が{counts['needs_negotiation']}件")
    if counts.get("needs_contact"):
        parts.append(f"連絡先探索が必要な有望案件が{counts['needs_contact']}件")
    if counts.get("needs_email"):
        parts.append(f"メール生成待ちが{counts['needs_email']}件")

    head = "、".join(parts) if parts else "着手可能な案件は多くありません"
    tail = ""
    if top_action is not None:
        tail = (
            f" まずは最重要の「{top_action['title']}」から"
            f"（{top_action['decision_label']}／{top_action['next_action']}）着手しましょう。"
        )
    elif counts.get("data_insufficient"):
        tail = (
            f" 判断材料が不足している案件が{counts['data_insufficient']}件あります。"
            "連絡先探索・企業リサーチでデータを補完しましょう。"
        )
    return f"{head}あります。{tail}".strip()
