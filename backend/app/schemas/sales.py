"""営業ワークフロー / 今日営業する案件 / ダッシュボードのスキーマ（pydantic v2）。"""
from __future__ import annotations

from pydantic import BaseModel

from app.models.project import SalesStatus


class WorkflowStep(BaseModel):
    key: str          # research / contact / email / dm
    label: str
    done: bool


class WorkflowChannel(BaseModel):
    key: str          # contact_form / instagram / linkedin / facebook / ... / gmail
    label: str
    url: str
    recommended: bool = False


class WorkflowOut(BaseModel):
    project_id: int
    sales_status: SalesStatus
    steps: list[WorkflowStep]
    channels: list[WorkflowChannel]
    priority_score: int
    stars: int
    ready_to_sell: bool


class SalesStatusUpdate(BaseModel):
    sales_status: SalesStatus


class TodayProject(BaseModel):
    project_id: int
    title: str
    source_site: str
    sales_status: SalesStatus
    priority_score: int
    stars: int
    reasons: list[str]


class TodayListOut(BaseModel):
    items: list[TodayProject]


class RankingItem(BaseModel):
    """AI 営業優先ランキングの 1 件（Executive Summary を統合）。"""

    project_id: int
    rank: int
    title: str
    source_site: str
    score: int
    stars: int
    sales_target: str            # "yes" / "no" / "要確認"
    recommended_channel: str
    recommended_action: str
    product_category: str
    japan_sales_status: str
    japan_distributor_status: str
    contact_status: str
    japan_market_fit: str
    reasons: list[str]
    cautions: list[str]


class RankingListOut(BaseModel):
    items: list[RankingItem]


class TaskItem(BaseModel):
    """「今日やること」の 1 件（営業アシスタントの案件）。"""

    project_id: int
    title: str
    source_site: str
    sales_status: SalesStatus
    latest_score: int | None = None
    # 営業優先度（0〜100）と星評価。
    priority_score: int = 0
    stars: int = 0
    # 連絡先/営業メールの有無（アクションボタンの出し分けに使う）。
    has_contact: bool = False
    has_email: bool = False
    # 最終営業からの経過日数とフォロー優先度（normal/high/final）。フォロー以外は None。
    days_since_last_outreach: int | None = None
    follow_up_level: str | None = None
    # 営業適性の総合スコア（評価済みのみ）と評価有無・可視化理由。
    assessment_overall: int | None = None
    evaluated: bool = True
    visibility_reason: str | None = None
    # この案件を今やるべき理由（人間可読）。
    reasons: list[str] = []


class TodayTasksOut(BaseModel):
    to_contact: list[TaskItem]        # 今日営業する案件（連絡先あり・未営業）
    needs_contact: list[TaskItem] = []      # 今日連絡先探索（評価済みだが連絡先なし）
    needs_evaluation: list[TaskItem] = []   # 評価待ち（営業適性が未評価）
    followup: list[TaskItem]      # 今日フォローする案件（3日以上返信なし）
    replied: list[TaskItem]       # 返信あり
    negotiating: list[TaskItem]   # 商談中
    idle: list[TaskItem] = []     # 放置でよい案件（営業済みだが3日未満）


class SalesDashboardOut(BaseModel):
    ready_count: int          # 営業準備完了
    today_count: int          # 今日営業する件数
    awaiting_reply_count: int # 返信待ち
    replied_count: int        # 返信あり
    negotiating_count: int    # 商談中
    won_count: int            # 契約数
    contacted_count: int      # 営業済み


# ===== 営業 AI コパイロット =====
class CopilotFunding(BaseModel):
    currency: str
    raised_amount: float | None = None
    goal_amount: float | None = None
    backers_count: int | None = None
    rate_pct: int | None = None


class CopilotRecommendation(BaseModel):
    score: int
    stars: int
    sales_target: str | None = None  # yes / no / 要確認


class CopilotSummary(BaseModel):
    """案件の営業サマリー（要件 1 の各項目を 1 つにまとめたもの）。"""

    product: str | None = None            # 商品概要
    company: str | None = None            # 会社概要
    japan_market_fit: str | None = None   # 日本市場との相性
    japan_sales_status: str | None = None # 日本販売状況
    funding: CopilotFunding               # 調達実績
    contact_status: str | None = None     # 連絡先の有無
    contact_person_found: bool = False    # 担当者の有無
    contact_person_name: str | None = None
    contact_person_title: str | None = None
    contact_person_department: str | None = None
    sales_status: str                     # 営業状況
    last_action: str                      # 最終アクション
    days_since_last_outreach: int | None = None
    next_action: str                      # 次にやるべきこと
    risks: list[str] = []                 # リスク
    recommendation: CopilotRecommendation # 推奨度


class CopilotCard(BaseModel):
    """1 案件の営業判断カード（サマリー＋判断＋理由＋アクション）。"""

    project_id: int
    title: str
    source_site: str
    decision: str          # sell_now / needs_contact / needs_followup / ...
    decision_label: str    # 日本語ラベル
    next_action: str       # 次の一手（短い命令形）
    actions: list[str]     # アクションボタンのキー
    reasons: list[str]     # なぜそう判断したか（必ず 1 件以上）
    priority_score: int
    stars: int
    urgency: int
    recommended_channel: str | None = None
    recommended_email: str | None = None
    summary: CopilotSummary


class CopilotDashboardOut(BaseModel):
    top_action: CopilotCard | None = None      # 今日の最重要アクション
    priority_sales: list[CopilotCard] = []     # 優先営業案件 TOP5
    needs_contact: list[CopilotCard] = []      # 連絡先探索すべき案件
    needs_email: list[CopilotCard] = []        # メール生成すべき案件
    followup: list[CopilotCard] = []           # フォローすべき案件
    drop_candidates: list[CopilotCard] = []    # 見送り候補
    data_insufficient: list[CopilotCard] = []  # データ不足案件
    counts: dict[str, int] = {}                # 判断カテゴリ別件数
    ai_comment: str                            # AI からのコメント
    scanned: int                               # 走査した案件数


# ===== 営業実行パイプライン（sales_outreach） =====
class TodayPriorityItem(BaseModel):
    """「今日営業する案件」1 件（優先度順）。"""

    project_id: int
    title: str
    source_site: str
    score: int
    reasons: list[str]
    contact_ready: bool
    recommended_action: str          # open_draft / generate_email / find_contact
    outreach_status: str | None = None
    recommended_language: str        # en / ko / zh / ja
    evaluated: bool = True


class TodayPriorityOut(BaseModel):
    items: list[TodayPriorityItem]


class OutreachGenerateIn(BaseModel):
    project_id: int


class OutreachOut(BaseModel):
    id: int
    project_id: int
    outreach_status: str
    priority_score: int | None = None
    generated_subject: str | None = None
    generated_body: str | None = None
    generated_language: str | None = None
    generated_variants: dict | None = None
    generated_at: str | None = None
    sent_at: str | None = None
    replied_at: str | None = None
    last_activity_at: str | None = None
    notes: str | None = None
    # 既存 Gmail compose を再利用した送信 URL（推奨言語・宛先入り。未生成なら None）。
    gmail_compose_url: str | None = None
    recipient: str | None = None
    # --- 送信後ワークフロー（0045） ---
    recipient_email: str | None = None
    sent_subject: str | None = None
    sent_body_snapshot: str | None = None
    sent_language: str | None = None
    followup_due_at: str | None = None
    followup_count: int = 0
    last_followup_at: str | None = None
    followups_remaining: int = 0
    reply_intent: str | None = None
    reply_summary: str | None = None
    reply_confidence: str | None = None
    last_reply_at: str | None = None
    user_edited: bool = False
    edited_at: str | None = None


class OutreachGenerateOut(BaseModel):
    outreach: OutreachOut
    job_id: int
    job_status: str
    created: bool           # 新規に生成ジョブを起動したか
    duplicate: bool         # 既に生成ジョブが動作中で重複起動を防いだか


# ---------------- 送信後ワークフロー（0045） ----------------
class OutreachDraftUpdate(BaseModel):
    """下書きの編集保存（同期・高速）。少なくとも 1 つは指定する。"""

    subject: str | None = None
    body: str | None = None
    language: str | None = None


class OutreachMarkSentIn(BaseModel):
    """「送信済みとして記録」。実メールは送らない。すべて任意（未指定は現下書きを採用）。"""

    language: str | None = None
    subject: str | None = None
    body: str | None = None
    recipient: str | None = None


class OutreachMarkSentOut(BaseModel):
    outreach: OutreachOut
    already_sent: bool      # 既に送信済みで冪等に無視したか


class FollowupGenerateOut(BaseModel):
    outreach: OutreachOut
    job_id: int | None = None
    job_status: str | None = None
    created: bool = False
    duplicate: bool = False
    eligible: bool = True
    reason: str | None = None   # 不適格時の理由（返信あり/上限到達など）


class ReplyIn(BaseModel):
    """手動で貼り付けた受信返信。"""

    incoming_body: str
    incoming_subject: str | None = None
    incoming_from: str | None = None


class ReplyAnalysis(BaseModel):
    intent: str
    sentiment: str
    detected_language: str
    summary: str
    confidence: str
    key_points: list[str] = []
    requested_actions: list[str] = []
    recommended_next_action: str = ""
    model: str = ""


class ReplyPreviewOut(BaseModel):
    """返信プレビュー（DB 非更新）。"""

    analysis: ReplyAnalysis


class ReplyConfirmOut(BaseModel):
    outreach: OutreachOut
    analysis: ReplyAnalysis


class ExecutionTaskItem(BaseModel):
    project_id: int
    title: str
    source_site: str | None = None
    outreach_status: str
    recipient: str | None = None
    sent_at: str | None = None
    sent_language: str | None = None
    followup_count: int = 0
    followups_remaining: int = 0
    followup_due_at: str | None = None
    days_overdue: int | None = None
    reply_intent: str | None = None
    reply_summary: str | None = None
    reply_confidence: str | None = None
    last_reply_at: str | None = None


class ExecutionTasksOut(BaseModel):
    follow_today: list[ExecutionTaskItem]
    overdue: list[ExecutionTaskItem]
    replied: list[ExecutionTaskItem]
    awaiting_reply: list[ExecutionTaskItem]
