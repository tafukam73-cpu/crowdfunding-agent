// バックエンド API のベース URL。ブラウザからアクセスするため公開環境変数を使う。
export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

// 画面表示用 GET の既定タイムアウト（ミリ秒）。応答が無いまま画面が固まるのを防ぐ。
export const DEFAULT_TIMEOUT_MS = 12000;

// fetch にタイムアウト（AbortController）を付与する共通ヘルパー。
// 一定時間で必ず打ち切り、「ページが応答しません」で固まらないようにする。
// cache は既定で no-store（常に最新の保存済みデータを読む）。
export async function apiFetch(
  path: string,
  init: RequestInit = {},
  timeoutMs = DEFAULT_TIMEOUT_MS
): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(`${API_BASE}${path}`, {
      cache: "no-store",
      ...init,
      signal: controller.signal,
    });
  } catch (e) {
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(
        `タイムアウト：${Math.round(timeoutMs / 1000)}秒以内に応答がありませんでした`
      );
    }
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

export type SourceSite =
  | "kickstarter"
  | "indiegogo"
  | "wadiz"
  | "ulule"
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

// 営業ワークフロー上の営業状況（既存の status とは別軸）
export type SalesStatus =
  | "not_started"
  | "ready"
  | "contacted"
  | "awaiting_reply"
  | "replied"
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
  // HTML 除去済みの読みやすい概要（表示はこちらを優先）
  description_clean: string | null;
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
  sales_status: SalesStatus;
  latest_score: number | null;
  latest_recommendation: Recommendation | null;
  maker_id: number | null;
  latest_availability: AvailabilityVerdict | null;
  latest_availability_at: string | null;
  // 商品性 / 営業対象判定（Ulule 案件のみ算出。それ以外は null / true）
  physical_product_score: number | null;
  sales_target_score: number | null;
  is_sales_target_candidate: boolean;
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

export type EmailTone =
  | "professional"
  | "friendly"
  | "executive"
  | "short"
  | "detailed";

export type EmailDraft = {
  id: number;
  project_id: number;
  email_type: EmailType;
  subject: string;
  body: string;
  language: string;
  model: string;
  subject_options: string[] | null;
  selected_subject: string | null;
  tone: string | null;
  japanese_summary: string | null;
  personalization_context: PersonalizationContext | null;
  personalized_compliment: string | null;
  product_highlights: string[] | null;
  provider: string | null;
  provider_draft_id: string | null;
  created_at: string;
};

export type PersonalizationContext = {
  product_name?: string;
  key_features?: string[];
  impressive_points?: string[];
  japan_market_angle?: string;
  maker_appeal?: string;
  recommended_opening_sentence?: string;
  personalized_compliment?: string;
  product_highlights?: string[];
};

export const EMAIL_TONE_LABELS: Record<EmailTone, string> = {
  professional: "Professional（標準・丁寧）",
  friendly: "Friendly（親しみやすい）",
  executive: "Executive（経営者向け・簡潔）",
  short: "Short（短文）",
  detailed: "Detailed（詳しめ）",
};

export const EMAIL_TONE_ORDER: EmailTone[] = [
  "professional",
  "friendly",
  "executive",
  "short",
  "detailed",
];

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

// Ulule 案件で AI 評価に付与される追加スコア軸（英語キー → 表示名）
export const ULULE_AXIS_LABELS: Record<string, string> = {
  europe_design_score: "Europe Design",
  sustainability_score: "Sustainability",
  craftsmanship_score: "Craftsmanship",
  gift_potential_score: "Gift Potential",
  japan_lifestyle_fit_score: "Japan Lifestyle Fit",
  premium_brand_potential_score: "Premium Brand Potential",
};

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
  candidates_only?: boolean;
  sort?: string;
  order?: "asc" | "desc";
  page?: number;
  page_size?: number;
};

export const SITE_LABELS: Record<SourceSite, string> = {
  kickstarter: "Kickstarter",
  indiegogo: "Indiegogo",
  wadiz: "Wadiz",
  ulule: "Ulule",
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
  "ulule",
];

export const SITE_COLORS: Record<SourceSite, string> = {
  kickstarter: "bg-green-100 text-green-700",
  indiegogo: "bg-pink-100 text-pink-700",
  wadiz: "bg-sky-100 text-sky-700",
  ulule: "bg-purple-100 text-purple-700",
  makuake: "bg-orange-100 text-orange-700",
  greenfunding: "bg-emerald-100 text-emerald-700",
  other: "bg-slate-100 text-slate-600",
};

// サイト名の安全な表示。未知・空欄は「不明」にして空欄表示を防ぐ。
export function siteLabel(site: string | null | undefined): string {
  if (!site) return "不明";
  return SITE_LABELS[site as SourceSite] ?? "不明";
}

// サイトバッジ色の安全な取得（未知・空欄は other 相当）。
export function siteColor(site: string | null | undefined): string {
  if (site && site in SITE_COLORS) return SITE_COLORS[site as SourceSite];
  return SITE_COLORS.other;
}

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
  const res = await apiFetch(`/scrape/stats?window=${window}`);
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

// --- 営業ワークフローの営業状況 ---
export const SALES_STATUS_LABELS: Record<SalesStatus, string> = {
  not_started: "未営業",
  ready: "営業準備完了",
  contacted: "営業済み",
  awaiting_reply: "返信待ち",
  replied: "返信あり",
  negotiating: "商談中",
  won: "契約",
  rejected: "見送り",
};

export const SALES_STATUS_COLORS: Record<SalesStatus, string> = {
  not_started: "bg-slate-100 text-slate-600",
  ready: "bg-sky-100 text-sky-700",
  contacted: "bg-amber-100 text-amber-700",
  awaiting_reply: "bg-yellow-100 text-yellow-700",
  replied: "bg-indigo-100 text-indigo-700",
  negotiating: "bg-purple-100 text-purple-700",
  won: "bg-green-100 text-green-700",
  rejected: "bg-red-100 text-red-700",
};

// 営業ワークフロー
export type WorkflowStep = {
  key: string;
  label: string;
  done: boolean;
};

export type WorkflowChannel = {
  key: string;
  label: string;
  url: string;
  recommended: boolean;
};

export type Workflow = {
  project_id: number;
  sales_status: SalesStatus;
  steps: WorkflowStep[];
  channels: WorkflowChannel[];
  priority_score: number;
  stars: number;
  ready_to_sell: boolean;
};

export type TodayProject = {
  project_id: number;
  title: string;
  source_site: SourceSite;
  sales_status: SalesStatus;
  priority_score: number;
  stars: number;
  reasons: string[];
};

export type SalesDashboard = {
  ready_count: number;
  today_count: number;
  awaiting_reply_count: number;
  replied_count: number;
  negotiating_count: number;
  won_count: number;
  contacted_count: number;
};

export async function fetchWorkflow(id: number): Promise<Workflow> {
  const res = await apiFetch(`/projects/${id}/workflow`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function updateSalesStatus(
  id: number,
  sales_status: SalesStatus
): Promise<Project> {
  const res = await fetch(`${API_BASE}/projects/${id}/sales-status`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sales_status }),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchTodayProjects(
  limit = 10
): Promise<TodayProject[]> {
  const res = await apiFetch(`/sales/today?limit=${limit}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const data = await res.json();
  return data.items as TodayProject[];
}

export async function fetchSalesDashboard(): Promise<SalesDashboard> {
  const res = await apiFetch(`/sales/dashboard`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchProjects(params: ListParams = {}): Promise<ProjectList> {
  const qs = new URLSearchParams();
  if (params.site) qs.set("site", params.site);
  if (params.status) qs.set("status", params.status);
  if (params.q) qs.set("q", params.q);
  if (params.min_score != null) qs.set("min_score", String(params.min_score));
  if (params.recommendation) qs.set("recommendation", params.recommendation);
  if (params.candidates_only) qs.set("candidates_only", "true");
  if (params.sort) qs.set("sort", params.sort);
  if (params.order) qs.set("order", params.order);
  qs.set("page", String(params.page ?? 1));
  qs.set("page_size", String(params.page_size ?? 20));

  const res = await apiFetch(`/projects?${qs.toString()}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchProject(id: number): Promise<Project> {
  const res = await apiFetch(`/projects/${id}`);
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

// SSR 用の名前付きエンティティ最小マップ（仏語の頻出アクセントを含む）。
const _ENTITY_MAP: Record<string, string> = {
  nbsp: " ", amp: "&", lt: "<", gt: ">", quot: '"', apos: "'",
  eacute: "é", egrave: "è", ecirc: "ê", euml: "ë", agrave: "à", acirc: "â",
  ccedil: "ç", ugrave: "ù", ucirc: "û", icirc: "î", iuml: "ï", ocirc: "ô",
  oelig: "œ", aelig: "æ", laquo: "«", raquo: "»", hellip: "…", rsquo: "’",
  lsquo: "‘", ldquo: "“", rdquo: "”", ndash: "–", mdash: "—", euro: "€",
};

// HTML エンティティをデコードする。ブラウザでは textarea を使って全エンティティを
// 確実に復号し（タグ除去後なのでスクリプト実行の恐れなし）、SSR では最小マップで代替する。
function decodeEntities(s: string): string {
  if (!s.includes("&")) return s;
  if (typeof document !== "undefined") {
    const el = document.createElement("textarea");
    el.innerHTML = s;
    return el.value;
  }
  return s
    .replace(/&#(\d+);/g, (_, d) => String.fromCharCode(parseInt(d, 10)))
    .replace(/&#x([0-9a-fA-F]+);/g, (_, h) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&([a-z]+);/gi, (m, name) => _ENTITY_MAP[name.toLowerCase()] ?? m);
}

// HTML 文字列から本文テキストだけを抽出する（正規表現ベース・サーバ/クライアント両対応）。
// バックエンドの description_clean が無い/空のときのフォールバック表示に使う。
// <img>/<figure> 等は内容ごと除去し、ブロック要素は改行に、残りのタグは除去する。
export function htmlToText(value: string | null | undefined): string {
  if (!value) return "";
  let text = value;
  // 画像・図・スクリプト・動画などは内容ごと除去（画像 URL・alt の混入を防ぐ）
  text = text.replace(
    /<(script|style|noscript|figure|picture|svg|video|iframe)\b[\s\S]*?<\/\1>/gi,
    " "
  );
  text = text.replace(/<(img|source|br)\b[^>]*\/?>/gi, (m) =>
    /^<br/i.test(m) ? "\n" : " "
  );
  // ブロック要素の境界は改行にする（インライン要素は連結）
  text = text.replace(
    /<\/?(p|div|li|ul|ol|tr|table|section|article|header|footer|h[1-6]|blockquote|pre)\b[^>]*>/gi,
    "\n"
  );
  // 残りのタグを除去 → エンティティをデコード
  text = decodeEntities(text.replace(/<[^>]+>/g, ""));
  // 空白・改行を整理
  text = text.replace(/\r\n?/g, "\n");
  const lines = text.split("\n").map((l) => l.replace(/[ \t ]+/g, " ").trim());
  return lines.filter(Boolean).join("\n").replace(/\n{3,}/g, "\n\n").trim();
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
  const res = await apiFetch(`/scrape/runs?limit=${limit}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 日次スケジューラの状態とサイト別の最終実行結果。
export async function fetchScheduleStatus(): Promise<ScheduleStatus> {
  const res = await apiFetch(`/scrape/last`);
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
  const res = await apiFetch(`/projects/${id}/evaluations`);
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
  const res = await apiFetch(`/usage/summary`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 営業メール下書きを 3 種別生成（同期）。tone でトーンを指定。
export async function generateEmailDrafts(
  id: number,
  tone: EmailTone = "professional"
): Promise<EmailDraft[]> {
  const res = await fetch(`${API_BASE}/projects/${id}/email-drafts/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tone }),
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 件名候補から選択した件名を保存（subject にも反映され、下書き作成で使われる）。
export async function selectEmailSubject(
  draftId: number,
  selectedSubject: string
): Promise<EmailDraft> {
  const res = await fetch(`${API_BASE}/email-drafts/${draftId}/subject`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected_subject: selectedSubject }),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

export async function fetchEmailDrafts(id: number): Promise<EmailDraft[]> {
  const res = await apiFetch(`/projects/${id}/email-drafts`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function fetchEmailProvider(): Promise<EmailProviderInfo> {
  const res = await apiFetch(`/email/provider`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== AI 企業リサーチ =====
export type ResearchStatus = "pending" | "completed" | "failed";

export type CompanyResearch = {
  id: number;
  project_id: number;
  maker_name: string | null;
  official_site_url: string | null;
  project_url: string | null;
  research_status: ResearchStatus;
  brand_summary: string | null;
  company_mission: string | null;
  product_summary: string | null;
  key_product_features: string[] | null;
  brand_strengths: string[] | null;
  differentiation_points: string[] | null;
  japan_market_fit: string | null;
  personalized_compliment: string | null;
  outreach_angles: string[] | null;
  risks_or_cautions: string[] | null;
  sources: string[] | null;
  model: string | null;
  raw_notes: string | null;
  created_at: string;
  updated_at: string;
};

// 最新の企業リサーチを取得（未実行なら 204 → null）。
export async function fetchCompanyResearch(
  id: number
): Promise<CompanyResearch | null> {
  const res = await apiFetch(`/projects/${id}/company-research`);
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 企業リサーチを実行（同期）。失敗時も failed として 200 で返る。
export async function runCompanyResearch(id: number): Promise<CompanyResearch> {
  const res = await fetch(`${API_BASE}/projects/${id}/company-research`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== 営業先連絡先探索 =====
export type DiscoveryStatus = "pending" | "completed" | "failed";

export type DiscoveredEmail = {
  email: string;
  score: number;
  tier: string;
  // maker / platform / monitoring / unknown（platform は UI 非表示）
  email_owner?: string | null;
  sources: string[];
};

export type ApproachOption = {
  channel: string;
  label: string;
  url: string | null;
  score: number;
  reason: string | null;
};

// AI 連絡先リサーチが提示し、既存フィルタで再検証済みの候補メール。
export type AiCandidateEmail = {
  email: string;
  score: number;
  confidence: string | null;
  reason: string | null;
  source_url: string | null;
  email_owner: string | null;
};

export type AiSource = {
  url: string;
  type: string | null;
  note: string | null;
};

// AI Web Research が調査した候補ページ。
export type WebCandidatePage = {
  url: string;
  type: string | null;
  ok?: boolean | null;
  emails?: number | null;
};

// 探索処理の集計（どこまで進んだかの可視化）。
export type WebDebugCounts = {
  queries: number | null;
  results: number | null;
  crawled: number | null;
  ok: number | null;
  failed: number | null;
  excluded: number | null;
  email_pages: number | null;
  // Kickstarter 等の埋め込み JSON "websites":[...]
  ks_websites_present?: boolean | null;
  ks_websites_count?: number | null;
  ks_websites_registered?: boolean | null;
};

export type DiscoveredPdf = {
  url: string;
  label: string | null;
  relevant: boolean | null;
};

// AI Web Research が生成したキーワード候補（検索戦略のデバッグ表示用）。
export type WebKeywordCandidates = {
  project_title: string | null;
  short_title: string | null;
  maker_name: string | null;
  brand_names: string[];
  official_domain: string | null;
  domain_name: string | null;
  source_site: string | null;
};

// 検索結果 1 件のスコアリング履歴（採用/除外理由つき）。
export type WebSearchResult = {
  query: string | null;
  url: string;
  title: string | null;
  score: number | null;
  kind: string | null; // social / pdf / page / excluded
  adopted: boolean | null;
  reason: string | null;
};

// 🧠 AI Document Reader の読解結果。
export type DocReaderEmail = {
  email: string;
  purpose: string | null;
  confidence: number;
  source_url: string | null;
  reason: string | null;
  email_owner: string | null;
};

export type DocReaderContactForm = {
  url: string;
  confidence: number;
  source_url: string | null;
};

export type DocReaderPerson = {
  name: string;
  title: string | null;
  linkedin_url: string | null;
  email: string | null;
  confidence: number;
  source_url: string | null;
  reason: string | null;
};

// 🏆 営業推奨連絡先（営業のしやすさで格付けしたメール）。
export type SalesContact = {
  email: string;
  stars: number; // 1〜5（5が最適）
  reason: string;
  category: string | null;
  score: number;
  email_owner: string | null;
  sources: string[];
};

export type ContactDiscovery = {
  id: number;
  project_id: number;
  maker_id: number | null;
  status: DiscoveryStatus;
  sales_contacts: SalesContact[];
  primary_email: string | null;
  primary_contact_form_url: string | null;
  official_site_url: string | null;
  instagram_url: string | null;
  facebook_url: string | null;
  twitter_url: string | null;
  linkedin_url: string | null;
  youtube_url: string | null;
  discovered_emails: DiscoveredEmail[] | null;
  discovered_forms: string[] | null;
  discovered_socials: Record<string, string> | null;
  searched_urls: string[] | null;
  confidence_score: number | null;
  contactability_score: number | null;
  recommended_channel: string | null;
  recommended_action: string | null;
  discovery_checklist: Record<string, boolean> | null;
  approach_options: ApproachOption[] | null;
  search_queries: string[] | null;
  evidence_summary: string | null;
  notes: string | null;
  error: string | null;
  // --- AI 連絡先リサーチ（自動抽出とは区別して表示） ---
  ai_researched: boolean;
  ai_primary_email: string | null;
  ai_contact_form_url: string | null;
  ai_instagram_url: string | null;
  ai_facebook_url: string | null;
  ai_linkedin_url: string | null;
  ai_candidate_emails: AiCandidateEmail[] | null;
  ai_search_queries: string[] | null;
  ai_sources: AiSource[] | null;
  ai_confidence_score: number | null;
  ai_recommended_channel: string | null;
  ai_notes: string | null;
  ai_model: string | null;
  ai_researched_at: string | null;
  // --- AI Web Research Mode（検索エンジン＋公式サイト横断クロール） ---
  web_researched: boolean;
  web_search_provider: string | null;
  web_debug_counts: WebDebugCounts | null;
  web_research_flow: string | null;
  web_keyword_candidates: WebKeywordCandidates | null;
  web_generated_queries: string[] | null;
  web_search_results: WebSearchResult[] | null;
  web_searched_queries: string[] | null;
  web_searched_urls: string[] | null;
  web_candidate_pages: WebCandidatePage[] | null;
  web_discovered_emails: DiscoveredEmail[] | null;
  web_discovered_forms: string[] | null;
  web_discovered_socials: Record<string, string> | null;
  web_discovered_pdfs: DiscoveredPdf[] | null;
  web_primary_email: string | null;
  web_primary_contact_form_url: string | null;
  web_recommended_channel: string | null;
  web_confidence_score: number | null;
  web_evidence_summary: string | null;
  web_notes: string | null;
  web_research_error: string | null;
  web_researched_at: string | null;
  // --- AI Document Reader ---
  doc_reader_researched: boolean;
  doc_reader_model: string | null;
  doc_reader_official_company_name: string | null;
  doc_reader_brand_names: string[] | null;
  doc_reader_official_site_url: string | null;
  doc_reader_emails: DocReaderEmail[] | null;
  doc_reader_contact_forms: DocReaderContactForm[] | null;
  doc_reader_socials: Record<string, string> | null;
  doc_reader_people: DocReaderPerson[] | null;
  doc_reader_recommended_channel: string | null;
  doc_reader_recommended_contact: string | null;
  doc_reader_confidence_score: number | null;
  doc_reader_evidence_summary: string | null;
  doc_reader_missing_info: string[] | null;
  doc_reader_sources: AiSource[] | null;
  doc_reader_researched_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ApplyToCrmResult = {
  maker_id: number;
  contact_id: number | null;
  email: string | null;
  recorded: boolean;
};

// 最新の連絡先探索を取得（未実行なら 204 → null）。
export async function fetchContactDiscovery(
  id: number
): Promise<ContactDiscovery | null> {
  const res = await apiFetch(`/projects/${id}/contact-discovery`);
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 連絡先探索を実行（同期）。失敗時も failed として 200 で返る。
export async function runContactDiscovery(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(`${API_BASE}/projects/${id}/contact-discovery`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// AI 連絡先リサーチを実行（同期）。Claude 未設定時はモックで動作。
// 失敗時も ai_notes にエラーを記録して 200 で返る。
export async function runAiContactResearch(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/ai-research`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// AI Web Research を実行（同期）。検索エンジン＋公式サイト横断クロールで連絡先を
// 実調査する。失敗時も web_research_error を記録して 200 で返る。
export async function runWebResearch(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/web-research`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// AI Document Reader を実行（同期）。Claude 未設定時はモックで動作。
// AI が返したメール・人名は既存フィルタで再検証。失敗時も evidence にエラーを記録。
export async function runDocumentReader(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/document-reader`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// ===== Contact Hunter AI（担当者発見） =====
export type ContactPerson = {
  id: number;
  project_id: number;
  name: string | null;
  title: string | null;
  department: string | null;
  linkedin_url: string | null;
  email: string | null;
  email_source: string | null;
  source_url: string | null;
  confidence: number | null;
  priority: number | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

export type ApplyPersonToCrmResult = {
  maker_id: number;
  contact_id: number;
  name: string | null;
  recorded: boolean;
};

// 担当者ハントを実行（同期）。会社ではなく「誰に送るか」を出典付きで特定する。
// Claude 未設定時は決定的な HTML 抽出（モック）で動作する。
export async function runContactHunter(id: number): Promise<ContactPerson[]> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/contact-people`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 最新の担当者候補を営業優先度順で取得（未実行なら空配列）。
export async function fetchContactPeople(id: number): Promise<ContactPerson[]> {
  const res = await apiFetch(
    `/projects/${id}/contact-discovery/contact-people`
  );
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 担当者を CRM の Contact として追加（氏名・役職・部署・LinkedIn・メール）。
export async function applyContactPersonToCrm(
  id: number,
  contactPersonId: number
): Promise<ApplyPersonToCrmResult> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/contact-people/apply-to-crm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ contact_person_id: contactPersonId }),
    }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 問い合わせフォーム / SNS DM 用の短文アウトリーチ文。
export type OutreachMessage = {
  channel: string;
  channel_label: string;
  text: string;
  char_count: number;
};

// メール以外のチャネル向けの短文アウトリーチ文を生成（生成のみ・保存しない）。
// channel 省略時はサーバ側で推奨チャネルを使う。
export async function fetchOutreachMessage(
  id: number,
  channel?: string
): Promise<OutreachMessage> {
  const qs = channel ? `?channel=${encodeURIComponent(channel)}` : "";
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/outreach-message${qs}`,
    { cache: "no-store" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 発見したメールを CRM に反映（自動上書きせず担当者として追加）。
export async function applyDiscoveryToCrm(
  id: number,
  email?: string
): Promise<ApplyToCrmResult> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/apply-to-crm`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email: email || null }),
    }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// ===== AI Executive Summary（営業価値の一目要約） =====
export type SalesTarget = "yes" | "no" | "要確認";

export type ExecutiveChannel =
  | "email"
  | "contact_form"
  | "instagram"
  | "linkedin"
  | "facebook"
  | "manual_search";

export type ExecutiveSummary = {
  project_id: number;
  score: number;
  stars: number;
  sales_target: SalesTarget;
  recommended_action: string;
  recommended_channel: ExecutiveChannel;
  product_category: string;
  japan_sales_status: string;
  japan_distributor_status: string;
  contact_status: string;
  japan_market_fit: string;
  // 推奨送信先（営業推奨連絡先ランキングの最上位）
  recommended_email: string | null;
  recommended_email_reason: string | null;
  recommended_email_stars: number | null;
  // Contact Hunter（担当者発見）
  contact_person_found: boolean;
  contact_person_name: string | null;
  contact_person_title: string | null;
  contact_person_department: string | null;
  contact_person_priority: number | null;
  reasons: string[];
  cautions: string[];
};

export const EXECUTIVE_CHANNEL_LABELS: Record<ExecutiveChannel, string> = {
  email: "メール",
  contact_form: "問い合わせフォーム",
  instagram: "Instagram",
  linkedin: "LinkedIn",
  facebook: "Facebook",
  manual_search: "手動検索",
};

// 案件の Executive Summary を取得（都度算出。未評価でも 200 で返る）。
export async function fetchExecutiveSummary(
  id: number
): Promise<ExecutiveSummary> {
  const res = await apiFetch(`/projects/${id}/executive-summary`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== AI 営業優先ランキング =====
export type RankingSort =
  | "score"
  | "created_at"
  | "latest_score"
  | "contact"
  | "unsold";

export const RANKING_SORT_LABELS: Record<RankingSort, string> = {
  score: "営業価値順",
  created_at: "新着順",
  latest_score: "AI評価順",
  contact: "連絡先あり優先",
  unsold: "日本未販売優先",
};

export type RankingItem = {
  project_id: number;
  rank: number;
  title: string;
  source_site: string;
  score: number;
  stars: number;
  sales_target: SalesTarget;
  recommended_channel: ExecutiveChannel;
  recommended_action: string;
  product_category: string;
  japan_sales_status: string;
  japan_distributor_status: string;
  contact_status: string;
  japan_market_fit: string;
  reasons: string[];
  cautions: string[];
};

export type RankingParams = {
  limit?: number;
  site?: SourceSite | "";
  candidates_only?: boolean;
  unsold_only?: boolean;
  contact_only?: boolean;
  not_started_only?: boolean;
  ulule_only?: boolean;
  sort?: RankingSort;
};

// ===== 今日やること（営業状況で分類） =====
export type SalesTask = {
  project_id: number;
  title: string;
  source_site: string;
  sales_status: SalesStatus;
  latest_score: number | null;
};

export type TodayTasks = {
  to_contact: SalesTask[];
  followup: SalesTask[];
  replied: SalesTask[];
  negotiating: SalesTask[];
};

// トップページ「今日やること」を取得（営業状況で分類した案件リスト）。
export async function fetchSalesTasks(perGroup = 5): Promise<TodayTasks> {
  const res = await apiFetch(`/sales/tasks?per_group=${perGroup}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// AI 営業優先ランキングを取得（Executive Summary を統合してスコア順）。
export async function fetchSalesRanking(
  params: RankingParams = {}
): Promise<RankingItem[]> {
  const qs = new URLSearchParams();
  qs.set("limit", String(params.limit ?? 20));
  if (params.site) qs.set("site", params.site);
  qs.set("candidates_only", String(params.candidates_only ?? true));
  qs.set("unsold_only", String(params.unsold_only ?? false));
  qs.set("contact_only", String(params.contact_only ?? false));
  qs.set("not_started_only", String(params.not_started_only ?? false));
  qs.set("ulule_only", String(params.ulule_only ?? false));
  qs.set("sort", params.sort ?? "score");

  const res = await apiFetch(`/sales/ranking?${qs.toString()}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const data = await res.json();
  return data.items as RankingItem[];
}

// ===== 日本販売状況チェック =====
export type JapanSalesStatus = "pending" | "completed" | "failed";

// チャネルの販売/掲載状況
export type ChannelStatus = "found" | "limited" | "not_found" | "unknown";

export type ChannelFinding = {
  channel: string;
  label: string;
  status: ChannelStatus;
  search_url: string;
  note: string;
};

export type JapanSalesCheck = {
  id: number;
  project_id: number;
  maker_id: number | null;
  status: JapanSalesStatus;
  sales_value_stars: number | null;
  channels: ChannelFinding[] | null;
  search_queries: string[] | null;
  ai_comment: string | null;
  summary: string | null;
  model: string | null;
  notes: string | null;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export const CHANNEL_STATUS_LABELS: Record<ChannelStatus, string> = {
  found: "販売・掲載あり",
  limited: "一部のみ",
  not_found: "未確認",
  unknown: "不明",
};

// 最新の日本販売状況チェックを取得（未実行なら 204 → null）。
export async function fetchJapanSalesCheck(
  id: number
): Promise<JapanSalesCheck | null> {
  const res = await apiFetch(`/projects/${id}/japan-sales-check`);
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 日本販売状況チェックを実行（同期）。失敗時も failed として 200 で返る。
export async function runJapanSalesCheck(
  id: number
): Promise<JapanSalesCheck> {
  const res = await fetch(`${API_BASE}/projects/${id}/japan-sales-check`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== 返信メール AI サポート =====
export type ReplyTone =
  | "professional"
  | "friendly"
  | "concise"
  | "detailed"
  | "executive";

export const REPLY_TONE_LABELS: Record<ReplyTone, string> = {
  professional: "Professional（標準・丁寧）",
  friendly: "Friendly（親しみやすい）",
  concise: "Concise（簡潔）",
  detailed: "Detailed（詳しめ）",
  executive: "Executive（経営者向け）",
};

export const REPLY_TONE_ORDER: ReplyTone[] = [
  "professional",
  "friendly",
  "concise",
  "detailed",
  "executive",
];

export type ReplyStatus = "draft" | "completed" | "failed";

export const INTENT_LABELS: Record<string, string> = {
  interested: "関心あり",
  needs_more_info: "追加情報希望",
  asks_terms: "条件の質問",
  requests_call: "通話希望",
  not_interested: "見送り",
  already_has_distributor: "既存代理店あり",
  unclear: "意図不明",
};

export const SENTIMENT_LABELS: Record<string, string> = {
  positive: "前向き",
  neutral: "中立",
  negative: "慎重",
};

export const SENTIMENT_COLORS: Record<string, string> = {
  positive: "bg-emerald-100 text-emerald-700",
  neutral: "bg-slate-100 text-slate-600",
  negative: "bg-amber-100 text-amber-700",
};

export type ReplyAssist = {
  id: number;
  project_id: number;
  maker_id: number | null;
  incoming_subject: string | null;
  incoming_body: string;
  incoming_from: string | null;
  detected_language: string | null;
  japanese_summary: string | null;
  intent: string | null;
  sentiment: string | null;
  key_points: string[] | null;
  requested_actions: string[] | null;
  risks_or_cautions: string[] | null;
  recommended_next_action: string | null;
  reply_tone: string | null;
  reply_subject: string | null;
  reply_body: string | null;
  gmail_draft_id: string | null;
  gmail_web_link: string | null;
  model: string | null;
  status: ReplyStatus;
  error: string | null;
  created_at: string;
  updated_at: string;
};

export type ReplyGmailDraftResult = {
  provider: string;
  draft_id: string | null;
  status: string;
  to: string;
  web_link: string | null;
  detail: string | null;
};

export async function createReplyAssist(
  projectId: number,
  input: {
    incoming_subject?: string;
    incoming_body: string;
    incoming_from?: string;
    reply_tone: ReplyTone;
  }
): Promise<ReplyAssist> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/reply-assist`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

export async function fetchReplyAssists(
  projectId: number
): Promise<ReplyAssist[]> {
  const res = await apiFetch(`/projects/${projectId}/reply-assists`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

export async function createReplyGmailDraft(
  replyAssistId: number,
  to?: string
): Promise<ReplyGmailDraftResult> {
  const res = await fetch(
    `${API_BASE}/reply-assists/${replyAssistId}/gmail-draft`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ to: to || null }),
    }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
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
  const res = await apiFetch(`/projects/${id}/similar-japanese?limit=${limit}`);
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

  const res = await apiFetch(`/japanese-success?${qs.toString()}`);
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
  const res = await apiFetch(`/crm/makers?${qs.toString()}`);
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
  const res = await apiFetch(`/projects/${projectId}/availability-checks`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
