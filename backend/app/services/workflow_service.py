"""営業ワークフロー / 優先順位 / 今日営業する案件 / ダッシュボードの業務ロジック。

案件詳細の「営業ワークフロー」カードと、トップページの「今日営業する案件」・
営業ダッシュボードに必要な情報をまとめて算出する。

ワークフロー完了判定:
  ① AI企業リサーチ : completed な company_research が存在
  ② 連絡先探索     : contact_discovery が存在
  ③ 営業メール生成 : email_draft が存在
  ④ 短文DM生成     : DM/フォーム系チャネル（contact_form/instagram/linkedin/
                      facebook）の URL が見つかっている（=短文をすぐ生成できる）
  ⑤ 営業開始       : URL のあるチャネルの「開く」ボタンを提示
  ⑥ 営業済みにする : sales_status を contacted へ（ユーザー操作）

優先順位スコア（priority_score, 0〜100）は日本市場適性・連絡先取得・メール生成・
クラファン実績・商品性・営業状況を総合して算出する。純粋関数として切り出し、
DB 非依存でテストできるようにしている。
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import and_, exists, not_, or_, select
from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.contact_discovery import ContactDiscovery
from app.models.email_draft import EmailDraft
from app.models.project import (
    SALES_TARGET_SITES,
    Project,
    SalesStatus,
    SourceSite,
)

# 短文アウトリーチ（DM / フォーム）を生成できるチャネル
_DM_CHANNELS = ("contact_form", "instagram", "linkedin", "facebook")

# 「営業開始」で開けるチャネル定義（contact_discovery の URL カラム → 表示ラベル）。
# email は Gmail 作成リンクに変換する。表示順は使いやすさ順。
_CHANNEL_FIELDS: list[tuple[str, str, str]] = [
    # (key, contact_discovery のカラム名, 日本語ラベル)
    ("contact_form", "primary_contact_form_url", "問い合わせフォーム"),
    ("instagram", "instagram_url", "Instagram"),
    ("linkedin", "linkedin_url", "LinkedIn"),
    ("facebook", "facebook_url", "Facebook"),
    ("twitter", "twitter_url", "X / Twitter"),
    ("youtube", "youtube_url", "YouTube"),
    ("official_site", "official_site_url", "公式サイト"),
]

_SALES_TARGET_VALUES = [s.value for s in SALES_TARGET_SITES]

# 「準備完了（=今日営業できる）」とみなす営業状況。まだ営業を始めていないもの。
_READY_STATUSES = (SalesStatus.not_started.value, SalesStatus.ready.value)


def _gmail_compose_url(email: str) -> str:
    """Gmail の新規作成画面を宛先入りで開く URL。"""
    return f"https://mail.google.com/mail/?view=cm&fs=1&to={email}"


# --- 完了判定（EXISTS サブクエリ。一覧/集計でも使い回す） ---
def _research_done_clause():
    return exists().where(
        and_(
            CompanyResearch.project_id == Project.id,
            CompanyResearch.research_status == ResearchStatus.completed.value,
        )
    )


def _contact_done_clause():
    return exists().where(ContactDiscovery.project_id == Project.id)


def _email_done_clause():
    return exists().where(EmailDraft.project_id == Project.id)


def _funding_rate(raised, goal) -> float | None:
    if raised is None or goal is None:
        return None
    g = float(goal)
    if g <= 0:
        return None
    return float(raised) / g


def compute_priority_score(
    *,
    latest_score: int | None,
    research_done: bool,
    contact_available: bool,
    email_done: bool,
    raised_amount: Decimal | float | None,
    goal_amount: Decimal | float | None,
    backers_count: int | None,
    is_sales_target_candidate: bool,
    sales_status: str,
) -> int:
    """営業優先順位スコア（0〜100）。値が高いほど今日営業すべき。"""
    score = 0.0

    # 日本市場適性 / AI 総合スコア（最大 40）
    score += min(max(latest_score or 0, 0), 100) * 0.4

    # 連絡先取得（営業可能性に直結）
    if contact_available:
        score += 20
    # 営業メール生成済み（すぐ送れる）
    if email_done:
        score += 15
    # 企業リサーチ済み（パーソナライズできる）
    if research_done:
        score += 10

    # クラファン実績（達成率優先、なければ支援者数）
    rate = _funding_rate(raised_amount, goal_amount)
    if rate is not None and rate >= 1.0:
        score += 5
    elif (backers_count or 0) >= 300:
        score += 3

    # 商品性（営業対象候補。Ulule 以外は常に True）
    if is_sales_target_candidate:
        score += 5

    # 営業状況による調整（既に営業済み/終了は今日の優先度を下げる）
    if sales_status == SalesStatus.contacted.value:
        score -= 40
    elif sales_status == SalesStatus.awaiting_reply.value:
        score -= 20
    elif sales_status in (SalesStatus.won.value, SalesStatus.rejected.value):
        score -= 60

    return int(max(0, min(100, round(score))))


def stars_for(score: int) -> int:
    """priority_score を 5 段階（★1〜5）に変換。"""
    if score >= 80:
        return 5
    if score >= 60:
        return 4
    if score >= 40:
        return 3
    if score >= 20:
        return 2
    return 1


def _reasons(
    *,
    latest_score: int | None,
    research_done: bool,
    contact_available: bool,
    email_done: bool,
    raised_amount,
    goal_amount,
    sales_status: str,
) -> list[str]:
    out: list[str] = []
    if latest_score is not None:
        out.append(f"日本市場適性{latest_score}")
    if contact_available:
        out.append("連絡先あり")
    if email_done:
        out.append("メール生成済み")
    if research_done:
        out.append("企業リサーチ済み")
    rate = _funding_rate(raised_amount, goal_amount)
    if rate is not None and rate >= 1.0:
        out.append("クラファン成功実績")
    if sales_status in _READY_STATUSES:
        out.append("営業未実施")
    return out


def _available_channels(cd: ContactDiscovery | None) -> list[dict]:
    """contact_discovery から「開く」ボタン用のチャネル一覧を作る（URL があるものだけ）。"""
    channels: list[dict] = []
    if cd is None:
        return channels

    recommended = cd.recommended_channel
    for key, field, label in _CHANNEL_FIELDS:
        url = getattr(cd, field, None)
        if url:
            channels.append(
                {
                    "key": key,
                    "label": label,
                    "url": url,
                    "recommended": recommended == key,
                }
            )

    # メール（Gmail 作成リンク）
    if cd.primary_email:
        channels.append(
            {
                "key": "gmail",
                "label": "Gmail",
                "url": _gmail_compose_url(cd.primary_email),
                "recommended": recommended == "email",
            }
        )
    return channels


def compute_workflow(db: Session, project: Project) -> dict:
    """案件のワークフロー状態・チャネル・優先順位を算出して返す。"""
    research_done = bool(
        db.scalar(
            select(
                exists().where(
                    CompanyResearch.project_id == project.id,
                    CompanyResearch.research_status == ResearchStatus.completed.value,
                )
            )
        )
    )
    cd = db.scalars(
        select(ContactDiscovery)
        .where(ContactDiscovery.project_id == project.id)
        .order_by(ContactDiscovery.created_at.desc(), ContactDiscovery.id.desc())
        .limit(1)
    ).first()
    contact_done = cd is not None
    email_done = bool(
        db.scalar(
            select(exists().where(EmailDraft.project_id == project.id))
        )
    )

    channels = _available_channels(cd)
    dm_done = any(c["key"] in _DM_CHANNELS for c in channels)

    steps = [
        {"key": "research", "label": "AI企業リサーチ", "done": research_done},
        {"key": "contact", "label": "連絡先探索", "done": contact_done},
        {"key": "email", "label": "営業メール生成", "done": email_done},
        {"key": "dm", "label": "短文DM生成", "done": dm_done},
    ]

    priority_score = compute_priority_score(
        latest_score=project.latest_score,
        research_done=research_done,
        contact_available=contact_done,
        email_done=email_done,
        raised_amount=project.raised_amount,
        goal_amount=project.goal_amount,
        backers_count=project.backers_count,
        is_sales_target_candidate=project.is_sales_target_candidate,
        sales_status=project.sales_status,
    )

    return {
        "project_id": project.id,
        "sales_status": project.sales_status,
        "steps": steps,
        "channels": channels,
        "priority_score": priority_score,
        "stars": stars_for(priority_score),
        # 全ステップ完了かつ未営業なら「営業準備完了」
        "ready_to_sell": research_done
        and contact_done
        and email_done
        and project.sales_status in _READY_STATUSES,
    }


def today_projects(db: Session, *, limit: int = 10) -> list[dict]:
    """今日営業すべき案件（準備完了・未営業）を優先順位順に返す。"""
    prepared = and_(
        _research_done_clause(),
        _contact_done_clause(),
        _email_done_clause(),
    )
    # 全件を Python 側で再計算しないよう、AI 総合スコア上位だけに絞ってから優先度を
    # 算出する（priority_score は latest_score と強く相関するため上位で十分）。
    scan_cap = max(limit * 5, 50)
    stmt = (
        select(Project)
        .where(
            Project.source_site.in_(_SALES_TARGET_VALUES),
            Project.sales_status.in_(_READY_STATUSES),
            prepared,
        )
        .order_by(Project.latest_score.desc().nullslast())
        .limit(scan_cap)
    )
    rows = list(db.scalars(stmt))

    out: list[dict] = []
    for p in rows:
        # prepared フィルタ済みなので research/contact/email はすべて完了している
        score = compute_priority_score(
            latest_score=p.latest_score,
            research_done=True,
            contact_available=True,
            email_done=True,
            raised_amount=p.raised_amount,
            goal_amount=p.goal_amount,
            backers_count=p.backers_count,
            is_sales_target_candidate=p.is_sales_target_candidate,
            sales_status=p.sales_status,
        )
        out.append(
            {
                "project_id": p.id,
                "title": p.title,
                "source_site": p.source_site,
                "sales_status": p.sales_status,
                "priority_score": score,
                "stars": stars_for(score),
                "reasons": _reasons(
                    latest_score=p.latest_score,
                    research_done=True,
                    contact_available=True,
                    email_done=True,
                    raised_amount=p.raised_amount,
                    goal_amount=p.goal_amount,
                    sales_status=p.sales_status,
                ),
            }
        )

    out.sort(key=lambda r: r["priority_score"], reverse=True)
    return out[: max(1, limit)]


# --- トップページ「今日やること」（営業状況で分類した案件リスト） ---
# 営業フロー上の「次の一手」ごとに案件をまとめる。既存データの並べ替えのみ。
_TASK_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    ("to_contact", (SalesStatus.not_started.value, SalesStatus.ready.value)),
    ("followup", (SalesStatus.contacted.value, SalesStatus.awaiting_reply.value)),
    ("replied", (SalesStatus.replied.value,)),
    ("negotiating", (SalesStatus.negotiating.value,)),
]


def today_tasks(db: Session, *, per_group: int = 5) -> dict:
    """営業状況で分類した「今日やること」を返す（各グループ最大 per_group 件）。

    - to_contact : 未営業 / 準備完了（これから営業）
    - followup   : 営業済み / 返信待ち（フォローアップ）
    - replied    : 返信あり
    - negotiating: 商談中
    既存の projects を読むだけで、新たな算出や外部アクセスは行わない。
    """

    def _list(statuses: tuple[str, ...]) -> list[dict]:
        stmt = (
            select(Project)
            .where(
                Project.source_site.in_(_SALES_TARGET_VALUES),
                Project.sales_status.in_(statuses),
            )
            .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
            .limit(per_group)
        )
        return [
            {
                "project_id": p.id,
                "title": p.title,
                "source_site": p.source_site,
                "sales_status": p.sales_status,
                "latest_score": p.latest_score,
            }
            for p in db.scalars(stmt)
        ]

    return {key: _list(statuses) for key, statuses in _TASK_GROUPS}


# --- AI 営業優先ランキング（Executive Summary を統合） ---
# 連絡先ありとみなす推奨チャネル（manual_search 以外）
_RANKING_SORTS = ("score", "created_at", "latest_score", "contact", "unsold")


def _contact_exists_clause():
    """連絡先（メール / フォーム / SNS）が見つかっている案件の EXISTS 条件。"""
    return exists().where(
        and_(
            ContactDiscovery.project_id == Project.id,
            or_(
                ContactDiscovery.primary_email.isnot(None),
                ContactDiscovery.primary_contact_form_url.isnot(None),
                ContactDiscovery.instagram_url.isnot(None),
                ContactDiscovery.linkedin_url.isnot(None),
                ContactDiscovery.facebook_url.isnot(None),
            ),
        )
    )


def _ranking_sort_key(sort: str, project: Project, summary: dict):
    """ランキングの並び替えキー（reverse=True で降順）。"""
    from app.services import executive_summary_service as ess

    score = summary["score"]
    ls = project.latest_score if project.latest_score is not None else -1
    if sort == "created_at":
        return (project.created_at,)
    if sort == "latest_score":
        return (ls, score)
    if sort == "contact":
        has_contact = summary["recommended_channel"] in ess.CONTACT_CHANNELS
        return (1 if has_contact else 0, score)
    if sort == "unsold":
        unsold = summary["japan_sales_status"] == ess.JAPAN_STATUS_UNSOLD
        return (1 if unsold else 0, score)
    # 既定：営業価値スコア順（同点は AI 評価で）
    return (score, ls)


def ranking(
    db: Session,
    *,
    limit: int = 20,
    site: str | None = None,
    candidates_only: bool = True,
    unsold_only: bool = False,
    contact_only: bool = False,
    not_started_only: bool = False,
    ulule_only: bool = False,
    sort: str = "score",
) -> list[dict]:
    """AI 営業優先ランキングを返す（Executive Summary を統合してスコア順）。

    パフォーマンスのため、SQL で対象を絞り込み・上位 scan_cap 件に限定してから
    Executive Summary を算出する（全件は再計算しない）。unsold_only など JSON 由来の
    条件は算出後に Python で絞り込む。
    """
    # 遅延 import で循環参照を避ける
    from app.services import executive_summary_service as ess
    from app.services.project_service import _non_candidate_condition

    if sort not in _RANKING_SORTS:
        sort = "score"

    conditions = [Project.source_site.in_(_SALES_TARGET_VALUES)]
    if site:
        conditions.append(Project.source_site == site)
    if ulule_only:
        conditions.append(Project.source_site == SourceSite.ulule.value)
    if candidates_only:
        conditions.append(not_(_non_candidate_condition()))
    if not_started_only:
        conditions.append(Project.sales_status.in_(_READY_STATUSES))
    if contact_only:
        conditions.append(_contact_exists_clause())

    # 算出対象を上位に限定（並びの主軸で事前ソートしてから cap）。
    # Executive Summary を全件算出しないよう、AI スコア上位の少数だけに絞る。
    scan_cap = max(limit * 2, 30)
    stmt = select(Project)
    for c in conditions:
        stmt = stmt.where(c)
    if sort == "created_at":
        stmt = stmt.order_by(Project.created_at.desc())
    else:
        stmt = stmt.order_by(Project.latest_score.desc().nullslast())
    stmt = stmt.limit(scan_cap)

    rows = list(db.scalars(stmt))

    enriched: list[tuple[Project, dict]] = []
    for p in rows:
        summary = ess.build_summary(db, p)
        if unsold_only and summary["japan_sales_status"] != ess.JAPAN_STATUS_UNSOLD:
            continue
        enriched.append((p, summary))

    enriched.sort(key=lambda ps: _ranking_sort_key(sort, ps[0], ps[1]), reverse=True)

    items: list[dict] = []
    for i, (p, summary) in enumerate(enriched[: max(1, limit)], start=1):
        items.append(
            {
                **summary,
                "rank": i,
                "title": p.title,
                "source_site": p.source_site,
            }
        )
    return items


def dashboard_summary(db: Session) -> dict:
    """営業ダッシュボード用の集計（準備完了・今日営業件数・返信待ち・商談中・契約数）。"""

    def _count(*conds) -> int:
        stmt = select(Project).where(Project.source_site.in_(_SALES_TARGET_VALUES))
        for c in conds:
            stmt = stmt.where(c)
        # COUNT に置き換え（サブクエリ EXISTS を含むため from select 経由）
        from sqlalchemy import func

        count_stmt = select(func.count()).select_from(stmt.subquery())
        return int(db.scalar(count_stmt) or 0)

    prepared = and_(
        _research_done_clause(),
        _contact_done_clause(),
        _email_done_clause(),
    )

    ready_count = _count(Project.sales_status.in_(_READY_STATUSES), prepared)

    return {
        # 営業準備完了（=今日営業できる件数）
        "ready_count": ready_count,
        "today_count": ready_count,
        "awaiting_reply_count": _count(
            Project.sales_status == SalesStatus.awaiting_reply.value
        ),
        "replied_count": _count(Project.sales_status == SalesStatus.replied.value),
        "negotiating_count": _count(
            Project.sales_status == SalesStatus.negotiating.value
        ),
        "won_count": _count(Project.sales_status == SalesStatus.won.value),
        "contacted_count": _count(
            Project.sales_status == SalesStatus.contacted.value
        ),
    }
