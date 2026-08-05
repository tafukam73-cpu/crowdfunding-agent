"""案件の業務ロジック（CRUD・検索）。"""
from __future__ import annotations

from sqlalchemy import asc, desc, func, select
from sqlalchemy.orm import Session

from datetime import datetime, timezone

from app.models.project import (
    JAPANESE_SUCCESS_SITES,
    SALES_TARGET_SITES,
    Project,
    ProjectStatus,
    SalesStatus,
    SourceSite,
    can_transition_sales_status,
    normalize_sales_status,
    not_archived_clause,
)
from app.models.project_status_event import ProjectStatusEvent, StatusChangeSource
from app.schemas.project import ProjectCreate, ProjectUpdate
from app.util.text import clean_description


class InvalidStatusTransition(ValueError):
    """許可されていない sales_status 遷移。ルーターで 409 に変換する。"""

    def __init__(self, from_status: str | None, to_status: str) -> None:
        self.from_status = normalize_sales_status(from_status)
        self.to_status = normalize_sales_status(to_status)
        super().__init__(
            f"営業状況を {self.from_status} から {self.to_status} へは変更できません"
        )


def record_status_event(
    db: Session,
    project: Project,
    *,
    from_status: str | None,
    to_status: str,
    source: str = StatusChangeSource.manual.value,
    note: str | None = None,
    commit: bool = True,
) -> ProjectStatusEvent:
    """sales_status の遷移履歴を 1 行追記する（正規化後の値で保存）。"""
    event = ProjectStatusEvent(
        project_id=project.id,
        from_status=normalize_sales_status(from_status) if from_status else None,
        to_status=normalize_sales_status(to_status),
        change_source=source,
        note=note,
    )
    db.add(event)
    if commit:
        db.commit()
    return event


def list_status_events(
    db: Session, project_id: int, *, limit: int = 100
) -> list[ProjectStatusEvent]:
    """案件の sales_status 変更履歴を新しい順に取得する。"""
    return list(
        db.scalars(
            select(ProjectStatusEvent)
            .where(ProjectStatusEvent.project_id == project_id)
            .order_by(ProjectStatusEvent.created_at.desc(), ProjectStatusEvent.id.desc())
            .limit(limit)
        )
    )

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


def get_project(db: Session, project_id: int) -> Project | None:
    return db.get(Project, project_id)


def list_projects(
    db: Session,
    *,
    site: SourceSite | None = None,
    status: ProjectStatus | None = None,
    sales_status: str | None = None,
    category: str | None = None,
    q: str | None = None,
    min_score: int | None = None,
    recommendation: str | None = None,
    qualification: str | None = None,
    archived: bool = False,
    sort: str = "created_at",
    order: str = "desc",
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Project], int]:
    """フィルタ・ソート・ページング付きで案件を取得する。

    archived=False（既定）は営業対象内（未除外）のみ、archived=True は営業対象外
    （除外済み）案件のみを返す（「除外済み案件」画面用）。

    Returns: (items, total)
    """
    # 営業対象（Kickstarter / Indiegogo / Wadiz）のみ。日本の成功事例
    # （Makuake / GreenFunding）が混入していても一覧には出さない。
    conditions = [Project.source_site.in_(_SALES_TARGET_VALUES)]
    # 営業対象外（ソフトデリート）の絞り込み。除外済み一覧のときだけ archived を出す。
    if archived:
        conditions.append(Project.archived_at.is_not(None))
    else:
        conditions.append(not_archived_clause())
    if site is not None:
        conditions.append(Project.source_site == site.value)
    if status is not None:
        conditions.append(Project.status == status.value)
    if sales_status:
        # contract_agreed で絞る場合は後方互換のため旧 won も含める。
        if sales_status == SalesStatus.contract_agreed.value:
            conditions.append(
                Project.sales_status.in_(
                    [SalesStatus.contract_agreed.value, SalesStatus.won.value]
                )
            )
        else:
            conditions.append(Project.sales_status == sales_status)
    if category:
        conditions.append(Project.category == category)
    if q:
        like = f"%{q}%"
        conditions.append(Project.title.ilike(like))
    if min_score is not None:
        conditions.append(Project.latest_score >= min_score)
    if recommendation:
        conditions.append(Project.latest_recommendation == recommendation)
    if qualification:
        # 営業対象除外判定（blocked / review / clear）。参照するのは projects の
        # スナップショット列で、これは **最新の pre_research 判定だけ** を保持する
        # （送信可否 pre_outreach は一覧の絞り込み軸にしない）。
        conditions.append(Project.lead_qualification_decision == qualification)

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
    SalesStatus.contract_agreed.value: "契約合意しました。",
    SalesStatus.import_prep.value: "輸入準備を開始しました。",
    SalesStatus.jp_cf_prep.value: "日本クラファン準備を開始しました。",
    SalesStatus.selling.value: "日本販売を開始しました（販売中）。",
    SalesStatus.closed.value: "案件を終了しました。",
    SalesStatus.rejected.value: "見送りにしました。",
}


def update_sales_status(
    db: Session,
    project: Project,
    sales_status: SalesStatus,
    *,
    source: str = StatusChangeSource.manual.value,
    note: str | None = None,
    enforce_transition: bool = True,
) -> Project:
    """営業ワークフローの営業状況を更新し、履歴記録と CRM 反映を行う。

    - 厳密な状態機械：許可されていない遷移は InvalidStatusTransition を送出する
      （enforce_transition=False で自動同期など内部呼び出しはガードを緩められる）。
    - 遷移ごとに project_status_events に 1 行追記する（change_source つき）。
    - 意味のある遷移では、メーカーを作成・リンクして SalesActivity を追加し、
      メーカーの交渉ステータスも同期する。
    """
    # 遅延 import で循環参照を避ける
    from app.models.crm import ActivityKind, CrmStatus
    from app.schemas.crm import ActivityCreate
    from app.services import crm_service

    prev = normalize_sales_status(project.sales_status)
    target = normalize_sales_status(sales_status.value)

    # 同一状態への遷移は冪等（履歴・CRM 反映もしない）。
    if prev == target:
        return project

    if enforce_transition and not can_transition_sales_status(prev, target):
        raise InvalidStatusTransition(prev, target)

    project.sales_status = target
    # 履歴を追記（本更新と同一トランザクションでコミット）。
    record_status_event(
        db, project, from_status=prev, to_status=target, source=source, note=note,
        commit=False,
    )
    db.commit()
    db.refresh(project)

    summary = _SALES_ACTIVITY_SUMMARY.get(target)
    if not summary:
        return project

    # CRM 反映：メーカーが無ければ案件情報から作成・リンク
    maker, _created = crm_service.create_from_project(db, project)

    kind = (
        ActivityKind.email
        if target in (SalesStatus.contacted.value, SalesStatus.replied.value)
        else ActivityKind.note
    )
    crm_service.add_activity(
        db,
        maker.id,
        ActivityCreate(kind=kind, summary=summary, project_id=project.id),
    )

    # メーカーの交渉ステータスも同期（契約後フェーズはすべて won 扱い）。
    crm_map = {
        SalesStatus.contacted.value: CrmStatus.contacted,
        SalesStatus.awaiting_reply.value: CrmStatus.contacted,
        SalesStatus.replied.value: CrmStatus.contacted,
        SalesStatus.negotiating.value: CrmStatus.negotiating,
        SalesStatus.contract_agreed.value: CrmStatus.won,
        SalesStatus.import_prep.value: CrmStatus.won,
        SalesStatus.jp_cf_prep.value: CrmStatus.won,
        SalesStatus.selling.value: CrmStatus.won,
        SalesStatus.closed.value: CrmStatus.won,
        SalesStatus.rejected.value: CrmStatus.lost,
    }
    crm_status = crm_map.get(target)
    if crm_status is not None:
        maker.status = crm_status.value
        db.commit()

    db.refresh(project)
    return project


def bulk_update_sales_status(
    db: Session,
    project_ids: list[int],
    sales_status: SalesStatus,
    *,
    source: str = StatusChangeSource.manual.value,
    note: str | None = None,
) -> tuple[int, list[int]]:
    """複数案件の sales_status を一括更新する。

    許可されない遷移の案件はスキップし、その id を返す（一括で全体を止めない）。
    Returns: (updated_count, skipped_ids)
    """
    updated = 0
    skipped: list[int] = []
    projects = list(
        db.scalars(select(Project).where(Project.id.in_(project_ids)))
    )
    for project in projects:
        try:
            before = normalize_sales_status(project.sales_status)
            update_sales_status(
                db, project, sales_status, source=source, note=note,
            )
            # 同一状態（no-op）はスキップ扱いにしない＝更新カウントに含めない。
            if normalize_sales_status(project.sales_status) != before:
                updated += 1
        except InvalidStatusTransition:
            db.rollback()
            skipped.append(project.id)
    return updated, skipped


def sync_sales_status(
    db: Session,
    project: Project,
    target: str,
    *,
    source: str,
    only_from: set[str],
    note: str | None = None,
) -> Project:
    """営業アクションに伴う sales_status の自動前進（後退・上書きはしない）。

    現在の状態が only_from（前進を許す出発状態）に含まれるときだけ target へ進める。
    それ以外（既により先・契約後・決着済み）は変更しない。状態機械ガードは緩める
    （自動同期は 409 を投げず、後退だけを only_from で防ぐ）。
    """
    cur = normalize_sales_status(project.sales_status)
    if cur in only_from and cur != normalize_sales_status(target):
        return update_sales_status(
            db, project, SalesStatus(target), source=source, note=note,
            enforce_transition=False,
        )
    return project


def archive_project(
    db: Session, project: Project, reason: str | None = None
) -> Project:
    """案件を営業対象外にする（ソフトデリート）。

    archived_at に現在時刻を入れ、理由を保存する。既に対象外なら日時は変えず、
    理由が渡されたときだけ更新する（冪等）。関連データは一切削除しない。
    """
    if project.archived_at is None:
        project.archived_at = datetime.now(timezone.utc)
    if reason is not None:
        project.archive_reason = reason
    db.commit()
    db.refresh(project)
    return project


def unarchive_project(db: Session, project: Project) -> Project:
    """営業対象外を解除して通常一覧へ戻す（復元）。理由もクリアする。"""
    project.archived_at = None
    project.archive_reason = None
    db.commit()
    db.refresh(project)
    return project


def archive_projects(
    db: Session, project_ids: list[int], reason: str | None = None
) -> int:
    """複数案件を一括で営業対象外にする。更新した件数を返す。

    既に対象外の案件は archived_at を変えない（理由が渡されたときだけ更新）。
    存在しない ID は無視する。
    """
    if not project_ids:
        return 0
    now = datetime.now(timezone.utc)
    projects = list(
        db.scalars(select(Project).where(Project.id.in_(project_ids)))
    )
    for project in projects:
        if project.archived_at is None:
            project.archived_at = now
        if reason is not None:
            project.archive_reason = reason
    db.commit()
    return len(projects)


def unarchive_projects(db: Session, project_ids: list[int]) -> int:
    """複数案件を一括で復元する。更新した件数を返す。"""
    if not project_ids:
        return 0
    projects = list(
        db.scalars(select(Project).where(Project.id.in_(project_ids)))
    )
    for project in projects:
        project.archived_at = None
        project.archive_reason = None
    db.commit()
    return len(projects)


def delete_project(db: Session, project: Project) -> None:
    db.delete(project)
    db.commit()


# スクレイピング取り込み時に更新しないフィールド（ユーザー管理 / メタ）
_UPSERT_SKIP = {"status"}

# 詳細補完（enrichment）で埋めた項目。一覧の再スクレイプは一覧カードにこれらが
# 無いため None を送ってくる。既存の非 None 値を None で消さない（補完を保護する）。
# 「空値で既存値を消さない」ため、None のときだけ既存値を維持する（実値なら更新する）。
_UPSERT_PRESERVE_IF_NULL = {
    "maker_name",
    "maker_url",
    "category",
    "start_date",
    "end_date",
}


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
        # 詳細補完で埋めた項目は、一覧再スクレイプの None で既存値を消さない。
        if key in _UPSERT_PRESERVE_IF_NULL and value is None:
            continue
        setattr(existing, key, value)
    # description（収集項目）を更新したので表示用の概要も作り直す
    existing.description_clean = clean_description(existing.description)
    return existing, False
