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

from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import and_, exists, func, not_, or_, select
from sqlalchemy.orm import Session

from app.models.company_research import CompanyResearch, ResearchStatus
from app.models.contact_discovery import ContactDiscovery
from app.models.crm import SalesActivity
from app.models.email_draft import EmailDraft
from app.models.japanese_success import JapaneseSuccessProject
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


# --- トップページ「今日やること」（営業アシスタント） ---
# フォローアップの経過日数しきい値（最終営業日からの日数）。
_FOLLOWUP_MIN_DAYS = 3     # 3日以上返信なし → フォロー対象
_FOLLOWUP_HIGH_DAYS = 7    # 7日以上 → 優先度高
_FOLLOWUP_FINAL_DAYS = 14  # 14日以上 → 最終フォロー候補

# フォロー対象になり得る営業状況（メール送信済み・返信待ち）。
_FOLLOWUP_STATUSES = (SalesStatus.contacted.value, SalesStatus.awaiting_reply.value)
# 日本未販売の可能性が高いと見なす availability（未上陸 / 可能性あり）。
_JAPAN_UNSOLD_AVAILABILITY = ("not_landed", "possible")
# 「今日やること」から完全に除外する終了ステータス（成約 / 見送り）。
_TASK_DONE_STATUSES = (SalesStatus.won.value, SalesStatus.rejected.value)


def _followup_level(days: int | None) -> str | None:
    """最終営業からの経過日数をフォロー優先度に変換する。3日未満は None。"""
    if days is None or days < _FOLLOWUP_MIN_DAYS:
        return None
    if days >= _FOLLOWUP_FINAL_DAYS:
        return "final"
    if days >= _FOLLOWUP_HIGH_DAYS:
        return "high"
    return "normal"


_LEVEL_RANK = {"final": 3, "high": 2, "normal": 1, None: 0}


def _ids_with_contact(db: Session, ids: list[int]) -> set[int]:
    """連絡先（メール/フォーム/SNS）が見つかっている project_id 集合（バッチ）。"""
    if not ids:
        return set()
    rows = db.scalars(
        select(ContactDiscovery.project_id)
        .where(
            ContactDiscovery.project_id.in_(ids),
            or_(
                ContactDiscovery.primary_email.isnot(None),
                ContactDiscovery.primary_contact_form_url.isnot(None),
                ContactDiscovery.instagram_url.isnot(None),
                ContactDiscovery.linkedin_url.isnot(None),
                ContactDiscovery.facebook_url.isnot(None),
            ),
        )
        .distinct()
    )
    return {pid for pid in rows if pid is not None}


def _ids_with_email(db: Session, ids: list[int]) -> set[int]:
    """営業メール下書きがある project_id 集合（バッチ）。"""
    if not ids:
        return set()
    rows = db.scalars(
        select(EmailDraft.project_id).where(EmailDraft.project_id.in_(ids)).distinct()
    )
    return {pid for pid in rows if pid is not None}


def _last_outreach_map(db: Session, ids: list[int]) -> dict[int, datetime]:
    """project_id -> 最終営業日時。SalesActivity を優先し、無ければ EmailDraft。"""
    out: dict[int, datetime] = {}
    if not ids:
        return out
    for model, ts_col in (
        (SalesActivity, SalesActivity.occurred_at),
        (EmailDraft, EmailDraft.created_at),
    ):
        rows = db.execute(
            select(model.project_id, func.max(ts_col))
            .where(model.project_id.in_(ids))
            .group_by(model.project_id)
        ).all()
        for pid, ts in rows:
            if pid is None or ts is None:
                continue
            # SalesActivity を優先（先に入れた値は上書きしない）
            out.setdefault(pid, ts)
    return out


def _success_categories(db: Session) -> set[str]:
    """日本成功事例が存在するカテゴリ集合（類似成功事例の有無判定・1クエリ）。"""
    rows = db.scalars(
        select(JapaneseSuccessProject.category)
        .where(JapaneseSuccessProject.category.isnot(None))
        .distinct()
    )
    return {c.strip().lower() for c in rows if c and c.strip()}


def _days_since(now: datetime, ts: datetime | None) -> int | None:
    if ts is None:
        return None
    # タイムゾーン差異に強くする（naive は UTC とみなす）
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = now - ts
    return max(0, delta.days)


def _task_reasons(
    p: Project,
    *,
    priority_score: int,
    has_contact: bool,
    has_email: bool,
    success_cats: set[str],
    days: int | None,
    group: str,
) -> list[str]:
    """案件が今日やることに入る理由を人間可読で列挙する。"""
    out: list[str] = [f"営業価値スコア{priority_score}"]
    if has_contact:
        out.append("連絡先あり")
    if has_email:
        out.append("営業メール生成済み")
    if p.latest_availability in _JAPAN_UNSOLD_AVAILABILITY:
        out.append("日本未販売の可能性あり")
    rate = _funding_rate(p.raised_amount, p.goal_amount)
    if rate is not None and rate >= 3.0:
        site = SITE_LABELS_JA.get(p.source_site, p.source_site)
        out.append(f"{site}達成率300%以上")
    elif rate is not None and rate >= 1.0:
        out.append("クラファン成功実績")
    if (p.category or "").strip().lower() in success_cats:
        out.append("類似の日本成功事例あり")
    if group == "followup" and days is not None:
        if days >= _FOLLOWUP_FINAL_DAYS:
            out.append(f"{days}日返信なし・最終フォロー候補")
        else:
            out.append(f"{days}日返信なしのためフォロー推奨")
    return out


# ソース名の簡易日本語表記（理由文言用）。未知はそのまま。
SITE_LABELS_JA = {
    "kickstarter": "Kickstarter",
    "indiegogo": "Indiegogo",
    "wadiz": "Wadiz",
    "ulule": "Ulule",
}


def today_tasks(db: Session, *, per_group: int = 5) -> dict:
    """営業アシスタント「今日やること」を返す（各グループ最大 per_group 件）。

    グループ:
      - to_contact : 今日営業する案件（未営業/準備完了。営業価値スコア順）
      - followup   : 今日フォローする案件（メール送信済みで最終営業から3日以上）
      - replied    : 返信あり
      - negotiating: 商談中
      - idle       : 放置でよい案件（営業済みだが3日未満＝まだ返信待ち期間）
    成約(won)/見送り(rejected)はどのグループにも含めない。

    各案件は理由・優先度スコア・連絡先/メール有無・最終営業からの経過日数・
    フォロー優先度（normal/high/final）を含む dict で返す。
    """
    now = datetime.now(timezone.utc)
    target = Project.source_site.in_(_SALES_TARGET_VALUES)
    scan_cap = max(per_group * 6, 40)

    def _fetch(statuses: tuple[str, ...]) -> list[Project]:
        stmt = (
            select(Project)
            .where(target, Project.sales_status.in_(statuses))
            .order_by(Project.latest_score.desc().nullslast(), Project.updated_at.desc())
            .limit(scan_cap)
        )
        return list(db.scalars(stmt))

    tc_rows = _fetch(_READY_STATUSES)
    fu_rows = _fetch(_FOLLOWUP_STATUSES)
    rep_rows = _fetch((SalesStatus.replied.value,))
    neg_rows = _fetch((SalesStatus.negotiating.value,))

    all_rows = tc_rows + fu_rows + rep_rows + neg_rows
    all_ids = [p.id for p in all_rows]
    contact_ids = _ids_with_contact(db, all_ids)
    email_ids = _ids_with_email(db, all_ids)
    outreach = _last_outreach_map(db, [p.id for p in fu_rows])
    success_cats = _success_categories(db)

    def _enrich(p: Project, *, group: str, days: int | None = None) -> dict:
        has_contact = p.id in contact_ids
        has_email = p.id in email_ids
        score = compute_priority_score(
            latest_score=p.latest_score,
            research_done=False,
            contact_available=has_contact,
            email_done=has_email,
            raised_amount=p.raised_amount,
            goal_amount=p.goal_amount,
            backers_count=p.backers_count,
            is_sales_target_candidate=p.is_sales_target_candidate,
            sales_status=p.sales_status,
        )
        level = _followup_level(days) if group == "followup" else None
        return {
            "project_id": p.id,
            "title": p.title,
            "source_site": p.source_site,
            "sales_status": p.sales_status,
            "latest_score": p.latest_score,
            "priority_score": score,
            "stars": stars_for(score),
            "has_contact": has_contact,
            "has_email": has_email,
            "days_since_last_outreach": days,
            "follow_up_level": level,
            "reasons": _task_reasons(
                p, priority_score=score, has_contact=has_contact,
                has_email=has_email, success_cats=success_cats,
                days=days, group=group,
            ),
        }

    # 今日営業する案件：営業価値スコア順
    to_contact = [_enrich(p, group="to_contact") for p in tc_rows]
    to_contact.sort(key=lambda t: t["priority_score"], reverse=True)

    # フォロー（3日以上）と放置でよい（3日未満）に分ける
    followup: list[dict] = []
    idle: list[dict] = []
    for p in fu_rows:
        last = outreach.get(p.id) or p.updated_at
        days = _days_since(now, last)
        if days is not None and days >= _FOLLOWUP_MIN_DAYS:
            followup.append(_enrich(p, group="followup", days=days))
        else:
            idle.append(_enrich(p, group="idle", days=days))
    # フォロー優先度：final > high > normal、同レベルは経過日数が長い順
    followup.sort(
        key=lambda t: (
            _LEVEL_RANK.get(t["follow_up_level"], 0),
            t["days_since_last_outreach"] or 0,
        ),
        reverse=True,
    )

    replied = [_enrich(p, group="replied") for p in rep_rows]
    replied.sort(key=lambda t: t["priority_score"], reverse=True)
    negotiating = [_enrich(p, group="negotiating") for p in neg_rows]
    negotiating.sort(key=lambda t: t["priority_score"], reverse=True)

    return {
        "to_contact": to_contact[:per_group],
        "followup": followup[:per_group],
        "replied": replied[:per_group],
        "negotiating": negotiating[:per_group],
        "idle": idle[:per_group],
    }


# --- AI 営業優先ランキング（Executive Summary を統合） ---
# 連絡先ありとみなす推奨チャネル（manual_search 以外）
_RANKING_SORTS = ("score", "created_at", "latest_score", "contact", "unsold")

# 「今日営業すべき案件ランキング」の営業状況フィルター。
# 既定は "not_started"＝未営業（未接触/準備完了）のみを表示し、営業アクション済み
# （営業済み・返信待ち・返信あり・商談中・契約・見送り）はランキングから除外する。
# None は「営業状況で絞り込まない（すべて表示）」を意味する。
_RANKING_STATUS_FILTERS: dict[str, tuple[str, ...] | None] = {
    # 既定：未営業のみ（これから営業すべき案件だけ）
    "not_started": (SalesStatus.not_started.value, SalesStatus.ready.value),
    # すべて表示（営業状況で絞り込まない）
    "all": None,
    # 返事待ち
    "awaiting_reply": (SalesStatus.awaiting_reply.value,),
    # フォローアップ対象（営業済み＋返信待ち）
    "followup": (SalesStatus.contacted.value, SalesStatus.awaiting_reply.value),
    # 商談中
    "negotiating": (SalesStatus.negotiating.value,),
}
_DEFAULT_RANKING_STATUS_FILTER = "not_started"


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
    status_filter: str = _DEFAULT_RANKING_STATUS_FILTER,
    ulule_only: bool = False,
    sort: str = "score",
) -> list[dict]:
    """AI 営業優先ランキングを返す（Executive Summary を統合してスコア順）。

    既定では **未営業（未接触・準備完了）の案件だけ**を返す（``status_filter``）。
    営業アクション済み（営業済み・返信待ち・返信あり・商談中・契約・見送り）は
    ランキングから除外され、ステータス変更後に再取得すると即座に消える。
    ``status_filter="all"`` で営業状況の絞り込みを解除できる。

    パフォーマンスのため、SQL で対象を絞り込み・上位 scan_cap 件に限定してから
    Executive Summary を算出する（全件は再計算しない）。unsold_only など JSON 由来の
    条件は算出後に Python で絞り込む。
    """
    # 遅延 import で循環参照を避ける
    from app.services import executive_summary_service as ess
    from app.services.project_service import _non_candidate_condition

    if sort not in _RANKING_SORTS:
        sort = "score"

    # 営業状況フィルターの解決（後方互換：not_started_only=True は "not_started" 扱い）。
    effective_filter = "not_started" if not_started_only else status_filter
    if effective_filter not in _RANKING_STATUS_FILTERS:
        effective_filter = _DEFAULT_RANKING_STATUS_FILTER
    status_values = _RANKING_STATUS_FILTERS[effective_filter]

    conditions = [Project.source_site.in_(_SALES_TARGET_VALUES)]
    if site:
        conditions.append(Project.source_site == site)
    if ulule_only:
        conditions.append(Project.source_site == SourceSite.ulule.value)
    if candidates_only:
        conditions.append(not_(_non_candidate_condition()))
    # 既定で営業アクション済みステータスを除外（"all" のときは絞り込まない）。
    if status_values is not None:
        conditions.append(Project.sales_status.in_(status_values))
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
