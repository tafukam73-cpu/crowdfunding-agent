// バックエンド API のベース URL。ブラウザからアクセスするため公開環境変数を使う。
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type SourceSite =
  | "kickstarter"
  | "indiegogo"
  | "wadiz"
  | "makuake"
  | "greenfunding"
  | "other";
export type ProjectStatus =
  | "new"
  | "reviewing"
  | "contacted"
  | "negotiating"
  | "won"
  | "rejected";

export type Recommendation = "high" | "mid" | "low";

export type Project = {
  id: number;
  title: string;
  source_site: SourceSite;
  source_url: string | null;
  category: string | null;
  description: string | null;
  image_url: string | null;
  video_url: string | null;
  currency: string;
  goal_amount: number | null;
  raised_amount: number | null;
  backers_count: number | null;
  start_date: string | null;
  end_date: string | null;
  maker_name: string | null;
  maker_url: string | null;
  contact_info: string | null;
  status: ProjectStatus;
  latest_score: number | null;
  latest_recommendation: Recommendation | null;
  maker_id: number | null;
  latest_availability: AvailabilityVerdict | null;
  latest_availability_at: string | null;
  created_at: string;
  updated_at: string;
};

export type AvailabilityVerdict = "not_landed" | "possible" | "sold";

export const AVAILABILITY_LABELS: Record<AvailabilityVerdict, string> = {
  not_landed: "未上陸",
  possible: "可能性あり",
  sold: "日本販売済み",
};

export const AVAILABILITY_COLORS: Record<AvailabilityVerdict, string> = {
  not_landed: "bg-green-100 text-green-700",
  possible: "bg-amber-100 text-amber-700",
  sold: "bg-red-100 text-red-700",
};

export type AvailabilityHit = {
  id: number;
  site: string;
  title: string | null;
  url: string | null;
  match_score: number;
  created_at: string;
};

export type AvailabilityCheck = {
  id: number;
  project_id: number;
  verdict: AvailabilityVerdict;
  score: number;
  query: string | null;
  summary: string | null;
  engine: string;
  created_at: string;
  hits: AvailabilityHit[];
};

export type Evaluation = {
  id: number;
  project_id: number;
  total_score: number;
  recommendation: Recommendation;
  axis_scores: Record<string, number>;
  reasons: string | null;
  concerns: string | null;
  sales_comment: string | null;
  model: string;
  created_at: string;
};

export type EmailType = "initial_outreach" | "exclusive_rights" | "followup";

export type EmailDraft = {
  id: number;
  project_id: number;
  email_type: EmailType;
  subject: string;
  body: string;
  language: string;
  model: string;
  provider: string | null;
  provider_draft_id: string | null;
  created_at: string;
};

export type EmailProviderInfo = {
  provider: string;
  gmail_configured: boolean;
};

export type ProviderDraftResult = {
  provider: string;
  draft_id: string | null;
  status: string;
  to: string;
  web_link: string | null;
  detail: string | null;
};

export const EMAIL_TYPE_LABELS: Record<EmailType, string> = {
  initial_outreach: "初回営業",
  exclusive_rights: "独占販売権打診",
  followup: "フォローアップ",
};

export const EMAIL_TYPE_ORDER: EmailType[] = [
  "initial_outreach",
  "exclusive_rights",
  "followup",
];

export const REC_LABELS: Record<Recommendation, string> = {
  high: "高",
  mid: "中",
  low: "低",
};

export const REC_COLORS: Record<Recommendation, string> = {
  high: "bg-green-100 text-green-700",
  mid: "bg-amber-100 text-amber-700",
  low: "bg-slate-100 text-slate-600",
};

export type ProjectList = {
  items: Project[];
  total: number;
  page: number;
  page_size: number;
};

// 日本クラファン（Makuake 等）の成功案件。海外案件との比較用。
export type JapaneseSuccess = {
  id: number;
  platform: string;
  title: string;
  source_url: string | null;
  category: string | null;
  description: string | null;
  image_url: string | null;
  video_url: string | null;
  currency: string;
  goal_amount: number | null;
  raised_amount: number | null;
  backers_count: number | null;
  start_date: string | null;
  end_date: string | null;
  maker_name: string | null;
  maker_url: string | null;
  created_at: string;
  updated_at: string;
};

// 海外案件に対する類似成功事例（類似度・理由付き）。
export type SimilarSuccess = JapaneseSuccess & {
  match_score: number;
  match_reasons: string[];
};

export type JapaneseSuccessList = {
  items: JapaneseSuccess[];
  total: number;
  page: number;
  page_size: number;
};

export type ListParams = {
  site?: SourceSite | "";
  status?: ProjectStatus | "";
  q?: string;
  min_score?: number;
  recommendation?: Recommendation | "";
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export const SITE_LABELS: Record<SourceSite, string> = {
  kickstarter: "Kickstarter",
  indiegogo: "Indiegogo",
  wadiz: "Wadiz",
  makuake: "Makuake",
  greenfunding: "GreenFunding",
  other: "その他",
};

// 海外営業対象サイト。案件一覧（projects）のフィルタはこの3サイトのみを扱う。
// Makuake / GreenFunding は日本の成功事例（比較用）であり営業対象に含めない。
export const SALES_TARGET_SITES: SourceSite[] = [
  "kickstarter",
  "indiegogo",
  "wadiz",
];

export const SITE_COLORS: Record<SourceSite, string> = {
  kickstarter: "bg-green-100 text-green-700",
  indiegogo: "bg-pink-100 text-pink-700",
  wadiz: "bg-sky-100 text-sky-700",
  makuake: "bg-orange-100 text-orange-700",
  greenfunding: "bg-emerald-100 text-emerald-700",
  other: "bg-slate-100 text-slate-600",
};

export type ScrapeStatus = "running" | "success" | "error";

export type ScrapeRun = {
  id: number;
  site: SourceSite;
  status: ScrapeStatus;
  fetched_count: number;
  created_count: number;
  updated_count: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export type SiteLastRun = {
  site: SourceSite;
  last_run: ScrapeRun | null;
};

export type JobTrigger = "schedule" | "manual";
export type JobStatus = "running" | "success" | "partial" | "error" | "skipped";

export type JobRun = {
  id: number;
  trigger: JobTrigger;
  status: JobStatus;
  sites_succeeded: number;
  sites_failed: number;
  error: string | null;
  started_at: string;
  finished_at: string | null;
};

export const JOB_TRIGGER_LABELS: Record<JobTrigger, string> = {
  schedule: "日次自動",
  manual: "手動",
};

export const JOB_STATUS_LABELS: Record<JobStatus, string> = {
  running: "実行中",
  success: "成功",
  partial: "一部失敗",
  error: "失敗",
  skipped: "スキップ",
};

export const JOB_STATUS_COLORS: Record<JobStatus, string> = {
  running: "bg-blue-100 text-blue-700",
  success: "bg-green-100 text-green-700",
  partial: "bg-amber-100 text-amber-700",
  error: "bg-red-100 text-red-700",
  skipped: "bg-slate-100 text-slate-600",
};

export type ScheduleStatus = {
  enabled: boolean;
  cron: string;
  timezone: string;
  next_run_time: string | null;
  last_job: JobRun | null;
  sites: SiteLastRun[];
};

export const SCRAPE_STATUS_LABELS: Record<ScrapeStatus, string> = {
  running: "実行中",
  success: "成功",
  error: "失敗",
};

// ===== 取得監視（/scrape/stats） =====
export type SiteStats = {
  site: SourceSite;
  window: number;
  total: number;
  success: number;
  errors: number;
  network_errors: number;
  structure_errors: number;
  unknown_errors: number;
  http_403_count: number;
  success_rate: number | null; // 0.0〜1.0
  last_status: ScrapeStatus | null;
  last_run_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  structure_change_suspected: boolean;
  last_structure_error_at: string | null;
  degraded: boolean;
};

export type ScrapeStats = {
  window: number;
  threshold: number; // degraded 判定のしきい値（成功率）
  structure_change_suspected: boolean;
  degraded: boolean;
  sites: SiteStats[];
};

// サイト別の取得成功率・エラー種別内訳・構造変化の疑い（直近 window 件）。
export async function fetchScrapeStats(window = 20): Promise<ScrapeStats> {
  const res = await fetch(`${API_BASE}/scrape/stats?window=${window}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export const SCRAPE_STATUS_COLORS: Record<ScrapeStatus, string> = {
  running: "bg-blue-100 text-blue-700",
  success: "bg-green-100 text-green-700",
  error: "bg-red-100 text-red-700",
};

export const STATUS_LABELS: Record<ProjectStatus, string> = {
  new: "新規",
  reviewing: "検討中",
  contacted: "連絡済み",
  negotiating: "交渉中",
  won: "獲得",
  rejected: "見送り",
};

export const STATUS_COLORS: Record<ProjectStatus, string> = {
  new: "bg-slate-100 text-slate-700",
  reviewing: "bg-blue-100 text-blue-700",
  contacted: "bg-amber-100 text-amber-700",
  negotiating: "bg-purple-100 text-purple-700",
  won: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700",
};

export async function fetchProjects(params: ListParams = {}): Promise<ProjectList> {
  const qs = new URLSearchParams();
  if (params.site) qs.set("site", params.site);
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  if (params.min_score != null) qs.set("min_score", String(params.min_score));
  if (params.recommendation) qs.set("recommendation", params.recommendation);
  if (params.sort) qs.set("sort", params.sort);
  if (params.order) qs.set("order", params.order);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));

  const res = await fetch(`${API_BASE}/projects?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchProject(id: number): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function updateProjectStatus(
  id: number,
  status: ProjectStatus
): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}/status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export function formatMoney(amount: number | null, currency: string): string {
  if (amount == null) return "—";
  return `${currency} ${Math.round(amount).toLocaleString()}`;
}

export function fundingRate(p: Project): number | null {
  if (!p.goal_amount || !p.raised_amount) return null;
  return Math.round((p.raised_amount / p.goal_amount) * 100);
}

export function formatDateTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString("ja-JP", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// 収集をバックグラウンド開始。running な実行レコードが返る。
export async function runScrape(site?: SourceSite, limit = 10): Promise<ScrapeRun[]> {
  const res = await fetch(`${API_BASE}/scrape/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(site ? { site, limit } : { limit }),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchScrapeRuns(limit = 10): Promise<ScrapeRun[]> {
  const res = await fetch(`${API_BASE}/scrape/runs?limit=${limit}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 日次スケジューラの状態とサイト別の最終実行結果。
export async function fetchScheduleStatus(): Promise<ScheduleStatus> {
  const res = await fetch(`${API_BASE}/scrape/last`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 4サイト一括収集をバックグラウンド起動（日次ジョブの手動トリガ）。
export async function runAllScrape(): Promise<{ status: string }> {
  const res = await fetch(`${API_BASE}/scrape/run-all`, { method: "POST" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 単体評価（同期）。最新の評価結果を返す。
export async function evaluateProject(id: number): Promise<Evaluation> {
  const res = await fetch(`${API_BASE}/projects/${id}/evaluate`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchEvaluations(id: number): Promise<Evaluation[]> {
  const res = await fetch(`${API_BASE}/projects/${id}/evaluations`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 未評価をまとめてバックグラウンド評価。queued 件数を返す。
export async function evaluateRun(): Promise<{ queued: number }> {
  const res = await fetch(`${API_BASE}/evaluate/run`, { method: "POST" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export type EvaluateEstimate = {
  mode: string; // claude / mock
  model: string;
  count: number;
  est_input_tokens: number;
  est_output_tokens: number;
  est_cost_usd: number;
};

export async function fetchEvaluateEstimate(): Promise<EvaluateEstimate> {
  const res = await fetch(`${API_BASE}/evaluate/estimate`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export type UsageBucket = {
  cost_usd: number;
  input_tokens: number;
  output_tokens: number;
  calls: number;
};

export type UsageSummary = {
  today: UsageBucket;
  month: UsageBucket;
  total: UsageBucket;
};

export async function fetchUsageSummary(): Promise<UsageSummary> {
  const res = await fetch(`${API_BASE}/usage/summary`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 営業メール下書きを 3 種別生成（同期）。
export async function generateEmailDrafts(id: number): Promise<EmailDraft[]> {
  const res = await fetch(`${API_BASE}/projects/${id}/email-drafts/generate`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchEmailDrafts(id: number): Promise<EmailDraft[]> {
  const res = await fetch(`${API_BASE}/projects/${id}/email-drafts`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchEmailProvider(): Promise<EmailProviderInfo> {
  const res = await fetch(`${API_BASE}/email/provider`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 生成済み下書きを、設定中プロバイダー（Gmail/mock）に下書き作成。送信はしない。
export async function createProviderDraft(
  draftId: number,
  to?: string
): Promise<ProviderDraftResult> {
  const res = await fetch(`${API_BASE}/email-drafts/${draftId}/provider-draft`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ to: to || null }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// ===== メール設定（差出人・会社情報・署名） =====
export type EmailSettings = {
  id: number;
  company_name: string | null;
  sender_name: string | null;
  sender_title: string | null;
  sender_department: string | null;
  sender_email: string | null;
  phone: string | null;
  website_url: string | null;
  company_profile: string | null;
  signature_template: string | null;
  created_at: string;
  updated_at: string;
};

// 保存・編集フォーム用の入力型（全項目任意）。
export type EmailSettingsInput = {
  company_name?: string | null;
  sender_name?: string | null;
  sender_title?: string | null;
  sender_department?: string | null;
  sender_email?: string | null;
  phone?: string | null;
  website_url?: string | null;
  company_profile?: string | null;
  signature_template?: string | null;
};

// 保存済みのメール設定を取得。未登録なら null。
export async function fetchEmailSettings(): Promise<EmailSettings | null> {
  const res = await fetch(`${API_BASE}/email-settings`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// メール設定を作成/更新（1 件運用）。
export async function updateEmailSettings(
  data: EmailSettingsInput
): Promise<EmailSettings> {
  const res = await fetch(`${API_BASE}/email-settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 海外案件に類似する日本の成功事例を取得。
export async function fetchSimilarJapanese(
  id: number,
  limit = 3
): Promise<SimilarSuccess[]> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/similar-japanese?limit=${limit}`,
    { cache: "no-store" }
  );
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export type JapaneseSuccessParams = {
  platform?: string;
  category?: string;
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export async function fetchJapaneseSuccess(
  params: JapaneseSuccessParams = {}
): Promise<JapaneseSuccessList> {
  const qs = new URLSearchParams();
  if (params.platform) qs.set("platform", params.platform);
  if (params.category) qs.set("category", params.category);
  if (params.q) qs.set("q", params.q);
  if (params.sort) qs.set("sort", params.sort);
  if (params.order) qs.set("order", params.order);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));

  const res = await fetch(`${API_BASE}/japanese-success?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 日本クラファン成功案件を収集（同期・現状モック）。
// platform 指定なしで Makuake + GreenFunding を一括収集。
export async function collectJapaneseSuccess(platform?: string): Promise<{
  fetched: number;
  created: number;
  updated: number;
}> {
  const qs = platform ? `?platform=${encodeURIComponent(platform)}` : "";
  const res = await fetch(`${API_BASE}/japanese-success/collect${qs}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== CRM（営業管理） =====
export type CrmStatus = "lead" | "contacted" | "negotiating" | "won" | "lost";
export type ActivityKind = "email" | "call" | "meeting" | "note" | "other";

export const CRM_STATUS_LABELS: Record<CrmStatus, string> = {
  lead: "リード",
  contacted: "連絡済み",
  negotiating: "交渉中",
  won: "成約",
  lost: "見送り",
};

export const CRM_STATUS_COLORS: Record<CrmStatus, string> = {
  lead: "bg-slate-100 text-slate-700",
  contacted: "bg-amber-100 text-amber-700",
  negotiating: "bg-purple-100 text-purple-700",
  won: "bg-green-100 text-green-700",
  lost: "bg-red-100 text-red-700",
};

export const ACTIVITY_KIND_LABELS: Record<ActivityKind, string> = {
  email: "メール",
  call: "電話",
  meeting: "打ち合わせ",
  note: "メモ",
  other: "その他",
};

export type Maker = {
  id: number;
  name: string;
  website_url: string | null;
  country: string | null;
  status: CrmStatus;
  next_action: string | null;
  next_action_date: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type Contact = {
  id: number;
  maker_id: number;
  name: string;
  role: string | null;
  email: string | null;
  phone: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type Activity = {
  id: number;
  maker_id: number;
  contact_id: number | null;
  project_id: number | null;
  kind: ActivityKind;
  summary: string;
  occurred_at: string;
  created_at: string;
};

export type MakerDetail = Maker & {
  contacts: Contact[];
  activities: Activity[];
  project_ids: number[];
};

export type MakerList = {
  items: Maker[];
  total: number;
  page: number;
  page_size: number;
};

export type Reminder = {
  maker_id: number;
  maker_name: string;
  status: CrmStatus;
  next_action: string | null;
  next_action_date: string;
  overdue: boolean;
};

export type MakerParams = {
  status?: CrmStatus | "";
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export async function fetchMakers(params: MakerParams = {}): Promise<MakerList> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  if (params.sort) qs.set("sort", params.sort);
  if (params.order) qs.set("order", params.order);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));
  const res = await fetch(`${API_BASE}/crm/makers?${qs.toString()}`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchMaker(id: number): Promise<MakerDetail> {
  const res = await fetch(`${API_BASE}/crm/makers/${id}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function createMaker(data: Partial<Maker>): Promise<Maker> {
  const res = await fetch(`${API_BASE}/crm/makers`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function createMakerFromProject(projectId: number): Promise<Maker> {
  const res = await fetch(`${API_BASE}/crm/makers/from-project/${projectId}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function updateMaker(
  id: number,
  data: Partial<Maker>
): Promise<Maker> {
  const res = await fetch(`${API_BASE}/crm/makers/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function deleteMaker(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/crm/makers/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
}

export async function addContact(
  makerId: number,
  data: Partial<Contact>
): Promise<Contact> {
  const res = await fetch(`${API_BASE}/crm/makers/${makerId}/contacts`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function deleteContact(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/crm/contacts/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
}

export async function addActivity(
  makerId: number,
  data: { kind: ActivityKind; summary: string; contact_id?: number | null; project_id?: number | null }
): Promise<Activity> {
  const res = await fetch(`${API_BASE}/crm/makers/${makerId}/activities`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(data),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function deleteActivity(id: number): Promise<void> {
  const res = await fetch(`${API_BASE}/crm/activities/${id}`, { method: "DELETE" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
}

export async function fetchReminders(withinDays?: number): Promise<Reminder[]> {
  const qs = withinDays != null ? `?within_days=${withinDays}` : "";
  const res = await fetch(`${API_BASE}/crm/reminders${qs}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== 日本未上陸判定 =====
export async function runAvailabilityCheck(
  projectId: number
): Promise<AvailabilityCheck> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/availability-check`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchAvailabilityChecks(
  projectId: number
): Promise<AvailabilityCheck[]> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/availability-checks`, {
    cache: "no-store",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
