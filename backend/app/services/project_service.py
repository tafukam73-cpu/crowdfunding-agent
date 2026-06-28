"""案件の業務ロジック（CRUD・検索）。"""
from __future__ import annotations

from sqlalchemy import and_, asc, desc, func, not_, or_, select
from sqlalchemy.orm import Session

from app.ai.ulule import (
    MEMO_MARKER,
    NON_PRODUCT_KEYWORDS,
    PRODUCT_KEYWORDS,
    clean_description,
)
from app.models.project import (
    JAPANESE_SUCCESS_SITES,
    SALES_TARGET_SITES,
    Project,
    ProjectStatus,
    SalesStatus,
    SourceSite,
)
from app.schemas.project import ProjectCreate, ProjectUpdate

# 営業対象サイトの値（一覧クエリ用）。Makuake / GreenFunding は除外する。
_SALES_TARGET_VALUES = [s.value for s in SALES_TARGET_SITES]
_JAPANESE_SUCCESS_VALUES = {s.value for s in JAPANESE_SUCCESS_SITES}

# 並び替えに使えるカラム
SORTABLE = {
    "created_at": Project.created_at,
    "updated_at": Project.updated_at,
    "raised_amount": Project.raised_amount,
    "backers_count": Project.backers_count,
    "end_date": Project.end_date,
    "title": Project.title,
    "latest_score": Project.latest_score,
}


# 営業対象候補の判定に使う検索テキスト（title + description本文 + category を結合・小文字化）。
# description は取得メモ（[Ulule] 以降）を除いた本文だけを対象にする。メモには
# "Europe Design" 等が含まれ商品語判定を誤らせるため、Postgres の split_part で切り出す。
# モデル側の product_assessment（_text もメモを除外）と同じ語で判定し、ページングと整合させる。
def _search_text():
    body = func.split_part(func.coalesce(Project.description, ""), MEMO_MARKER, 1)
    return func.lower(
        func.coalesce(Project.title, "")
        + " "
        + body
        + " "
        + func.coalesce(Project.category, "")
    )


def _non_candidate_condition():
    """『営業対象外』の Ulule 案件を表す SQL 条件。

    Ulule 案件で、非商品語を含み、かつ商品語を一切含まないものを営業対象外とする
    （app.ai.ulule.product_assessment の is_sales_target_candidate と同じ定義）。
    """
    text = _search_text()
    has_non_product = or_(*[text.like(f"%{kw.lower()}%") for kw in NON_PRODUCT_KEYWORDS])
    has_product = or_(*[text.like(f"%{kw.lower()}%") for kw in PRODUCT_KEYWORDS])
    return and_(
        Project.source_site == SourceSite.ulule.value,
        has_non_product,
        not_(has_product),
    )


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def list_projects(
    db: Session,
    *,
    site: SourceSite | None = None,
    status: ProjectStatus | None = None,
    category: str | None = None,
    q: str | None = None,
    min_score: int | None = None,
    recommendation: str | None = None,
    candidates_only: bool = False,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Project], int]:
    """フィルタ・ソート・ページング付きで案件を取得する。

    Returns: (items, total)
    """
    # 営業対象（Kickstarter / Indiegogo / Wadiz）のみ。日本の成功事例
    # （Makuake / GreenFunding）が混入していても一覧には出さない。
    conditions = [Project.source_site.in_(_SALES_TARGET_VALUES)]
    if site is not None:
        conditions.append(Project.source_site == site.value)
    if status is not None:
        conditions.append(Project.status == status.value)
    if category:
        conditions.append(Project.category == category)
    if q:
        like = f"%{q}%"
        conditions.append(Project.title.ilike(like))
    if min_score is not None:
        conditions.append(Project.latest_score >= min_score)
    if recommendation:
        conditions.append(Project.latest_recommendation == recommendation)
    if candidates_only:
        # 営業対象候補のみ（営業対象外っぽい Ulule 案件を除外）。
        conditions.append(not_(_non_candidate_condition()))

    base = select(Project)
    count_stmt = select(func.count()).select_from(Project)
    for cond in conditions:
        base = base.where(cond)
        count_stmt = count_stmt.where(cond)

    total = db.scalar(count_stmt) or 0

    sort_col = SORTABLE.get(sort, Project.created_at)
    direction = asc if order == "asc" else desc
    # スコア等で NULL（未評価/欠損）は常に最後へ
    base = base.order_by(direction(sort_col).nullslast())

    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    base = base.offset((page - 1) * page_size).limit(page_size)

    items = list(db.scalars(base))
    return items, total


def create_project(db: Session, data: ProjectCreate) -> Project:
    project = Project(**data.model_dump())
    # Enum -> 値（文字列）へ
    project.source_site = data.source_site.value
    project.status = data.status.value
    # 生 HTML を除去した表示用の概要を生成して保存
    project.description_clean = clean_description(project.description)
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def update_project(db: Session, project: Project, data: ProjectUpdate) -> Project:
    payload = data.model_dump(exclude_unset=True)
    for key, value in payload.items():
        if key in {"source_site", "status"} and value is not None:
            value = value.value  # Enum -> 文字列
        setattr(project, key, value)
    # description が変わったら表示用の概要も作り直す
    if "description" in payload:
        project.description_clean = clean_description(project.description)
    db.commit()
    db.refresh(project)
    return project


def update_status(db: Session, project: Project, status: ProjectStatus) -> Project:
    project.status = status.value
    db.commit()
    db.refresh(project)
    return project


# 営業状況の遷移時に CRM へ自動記録する営業履歴の要約・種別。
# not_started / ready は「営業前」のため履歴は残さない。
_SALES_ACTIVITY_SUMMARY: dict[str, str] = {
    SalesStatus.contacted.value: "営業を開始しました（営業済み）。",
    SalesStatus.awaiting_reply.value: "返信待ちに変更しました。",
    SalesStatus.replied.value: "先方から返信がありました。",
    SalesStatus.negotiating.value: "商談中になりました。",
    SalesStatus.won.value: "契約成立しました。",
    SalesStatus.rejected.value: "見送りにしました。",
}


def update_sales_status(
    db: Session, project: Project, sales_status: SalesStatus
) -> Project:
    """営業ワークフローの営業状況を更新し、CRM に営業履歴を自動記録する。

    意味のある遷移（営業済み・返信あり・商談中・契約 など）のときは、
    必要に応じてメーカーを作成・リンクしたうえで SalesActivity を追加し、
    メーカーの交渉ステータスも同期する。
    """
    # 遅延 import で循環参照を避ける
    from app.models.crm import ActivityKind, CrmStatus
    from app.schemas.crm import ActivityCreate
    from app.services import crm_service

    prev = project.sales_status
    project.sales_status = sales_status.value
    db.commit()
    db.refresh(project)

    summary = _SALES_ACTIVITY_SUMMARY.get(sales_status.value)
    if not summary or prev == sales_status.value:
        return project

    # CRM 反映：メーカーが無ければ案件情報から作成・リンク
    maker = crm_service.create_from_project(db, project)

    kind = (
        ActivityKind.email
        if sales_status.value in (SalesStatus.contacted.value, SalesStatus.replied.value)
        else ActivityKind.note
    )
    crm_service.add_activity(
        db,
        maker.id,
        ActivityCreate(kind=kind, summary=summary, project_id=project.id),
    )

    # メーカーの交渉ステータスも同期
    crm_map = {
        SalesStatus.contacted.value: CrmStatus.contacted,
        SalesStatus.awaiting_reply.value: CrmStatus.contacted,
        SalesStatus.replied.value: CrmStatus.contacted,
        SalesStatus.negotiating.value: CrmStatus.negotiating,
        SalesStatus.won.value: CrmStatus.won,
        SalesStatus.rejected.value: CrmStatus.lost,
    }
    crm_status = crm_map.get(sales_status.value)
    if crm_status is not None:
        maker.status = crm_status.value
        db.commit()

    db.refresh(project)
    return project


def delete_project(db: Session, project: Project) -> None:
    db.delete(project)
    db.commit()


# スクレイピング取り込み時に更新しないフィールド（ユーザー管理 / メタ）
_UPSERT_SKIP = {"status"}


def upsert_by_source_url(db: Session, data: ProjectCreate) -> tuple[Project, bool]:
    """source_url をキーに upsert する。

    既存があれば収集項目を更新（営業ステータスは保持）、なければ新規作成。
    Returns: (project, created)  created=True なら新規。

    注意：コミットは行わない。呼び出し側（collector）でまとめてコミットする。
    """
    # 日本の成功事例（Makuake / GreenFunding）は projects には保存しない。
    # これらは japanese_success_service が japanese_success_projects に収集する。
    if data.source_site.value in _JAPANESE_SUCCESS_VALUES:
        raise ValueError(
            f"{data.source_site.value} は営業対象外のため projects に保存できません"
            "（japanese_success_projects に保存してください）"
        )

    existing: Project | None = None
    if data.source_url:
        existing = db.scalar(
            select(Project).where(Project.source_url == data.source_url)
        )

    payload = data.model_dump()
    payload["source_site"] = data.source_site.value
    payload["status"] = data.status.value

    if existing is None:
        project = Project(**payload)
        # 生 HTML を除去した表示用の概要を生成して保存
        project.description_clean = clean_description(project.description)
        db.add(project)
        return project, True

    for key, value in payload.items():
        if key in _UPSERT_SKIP:
            continue
        setattr(existing, key, value)
    # description（収集項目）を更新したので表示用の概要も作り直す
    existing.description_clean = clean_description(existing.description)
    return existing, False
