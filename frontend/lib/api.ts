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
  | "zeczec"
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
  zeczec: "Zeczec",
  ulule: "Ulule",
  makuake: "Makuake",
  greenfunding: "GreenFunding",
  other: "その他",
};

// 海外営業対象サイト。案件一覧（projects）のフィルタはこれらのサイトを扱う。
// Makuake / GreenFunding は日本の成功事例（比較用）であり営業対象に含めない。
export const SALES_TARGET_SITES: SourceSite[] = [
  "kickstarter",
  "indiegogo",
  "wadiz",
  "zeczec",
  "ulule",
];

export const SITE_COLORS: Record<SourceSite, string> = {
  kickstarter: "bg-green-100 text-green-700",
  indiegogo: "bg-pink-100 text-pink-700",
  wadiz: "bg-sky-100 text-sky-700",
  zeczec: "bg-amber-100 text-amber-700",
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

// Gmail の「作成（compose）」画面を開く URL を生成する。
// view=cm&fs=1 で新規メール作成画面（送信ではない）を開く。
// to / su(件名) / body は必ず encodeURIComponent する。
export function buildGmailComposeUrl(params: {
  to?: string;
  subject?: string;
  body?: string;
}): string {
  const parts = ["view=cm", "fs=1"];
  if (params.to) parts.push(`to=${encodeURIComponent(params.to)}`);
  if (params.subject) parts.push(`su=${encodeURIComponent(params.subject)}`);
  if (params.body) parts.push(`body=${encodeURIComponent(params.body)}`);
  return `https://mail.google.com/mail/?${parts.join("&")}`;
}

// Gmail 作成画面を新規タブで開く。ポップアップブロックで開けなかった場合は false を返す。
export function openGmailCompose(params: {
  to?: string;
  subject?: string;
  body?: string;
}): { opened: boolean; url: string } {
  const url = buildGmailComposeUrl(params);
  // noopener,noreferrer で開き元ページを保護する。
  const win =
    typeof window !== "undefined"
      ? window.open(url, "_blank", "noopener,noreferrer")
      : null;
  return { opened: Boolean(win), url };
}

// ===== URL バリデーション（参照元URL・公式サイトのダミー除外） =====
// example.com / dummy / sample / test / localhost / 127.0.0.1 / githubusercontent
// や kickstarter.com/projects/example/... などのダミー/プレースホルダーを弾く。
// バックエンドの url_validation.is_valid_business_url と対になる（表示前の最終防波堤）。
const DUMMY_URL_HOST_LABELS = new Set([
  "example", "dummy", "sample", "samples", "test", "tests", "testing",
  "placeholder", "localhost", "invalid", "yourdomain", "mydomain",
  "yourcompany", "mycompany", "domain", "acme", "foo", "bar", "baz",
  "githubusercontent",
]);
const DUMMY_URL_HOSTS = new Set(["127.0.0.1", "0.0.0.0", "::1", "localhost"]);
const DUMMY_URL_PATH_TOKENS = [
  "/projects/example", "/project/example", "/example/", "/user/example",
  "/creator/example", "/dummy/", "/sample/", "/test/",
];

export function isValidBusinessUrl(url: string | null | undefined): boolean {
  const raw = (url ?? "").trim();
  if (!raw) return false;
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    return false;
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") return false;
  const host = parsed.hostname.toLowerCase();
  if (!host || DUMMY_URL_HOSTS.has(host)) return false;
  if (host.includes("githubusercontent")) return false;
  for (const label of host.split(".")) {
    if (DUMMY_URL_HOST_LABELS.has(label)) return false;
  }
  const path = parsed.pathname.toLowerCase();
  if (DUMMY_URL_PATH_TOKENS.some((t) => path.includes(t))) return false;
  return true;
}

// 有効なビジネス URL のみを残す（順序維持・重複排除）。
export function filterBusinessUrls(
  urls: (string | null | undefined)[] | null | undefined
): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const u of urls ?? []) {
    const s = (u ?? "").trim();
    if (!s || seen.has(s)) continue;
    seen.add(s);
    if (isValidBusinessUrl(s)) out.push(s);
  }
  return out;
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

// ブラウザ↔サーバ間の接続断（長時間の同期処理でよく起きる）を判定する。
// これらは「バックエンドが失敗した」ではなく「応答を受け取れなかった」ケース。
// 生の TypeError: Failed to fetch を画面に出さないための共通判定。
export function isNetworkDropError(e: unknown): boolean {
  const msg = String(e);
  return (
    msg.includes("Failed to fetch") ||
    msg.includes("NetworkError") ||
    msg.includes("network error") ||
    msg.includes("ERR_") ||
    msg.includes("AbortError") ||
    msg.includes("aborted") ||
    msg.includes("タイムアウト")
  );
}

// 企業リサーチを実行（同期）。失敗時も failed として 200 で返る。
// バックエンドがエラー本文を返す場合はそれを含めて投げる（画面で詳細表示するため）。
export async function runCompanyResearch(id: number): Promise<CompanyResearch> {
  const res = await fetch(`${API_BASE}/projects/${id}/company-research`, {
    method: "POST",
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = await res.text();
    } catch {
      /* ignore */
    }
    throw new Error(`API error: ${res.status}${detail ? ` ${detail}` : ""}`);
  }
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
  // v3 再帰クロールの PDF 解析結果（抽出メール数・本文長）。任意。
  emails?: number | null;
  text_len?: number | null;
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
  creator_slug?: string | null;
  project_slug?: string | null;
  maker_ambiguous?: boolean | null;
};

// 1 検索クエリの診断（0件の原因究明用）。
export type SearchProviderResult = {
  provider: string | null;
  results: number | null;
  status: number | null;
  reason: string | null;
};

export type WebSearchDiagnostic = {
  query: string | null;
  provider: string | null;
  status: number | null;
  reason: string | null;
  results: number | null;
  fallback: string | null;
  urls: string[];
  providers?: SearchProviderResult[];
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

// 🕵️ AI Search Agent の探索ステップ。
export type SearchAgentStep = {
  step: number | null;
  action: string | null; // search / visit / skip / stop
  url: string | null;
  query: string | null;
  reason: string | null;
  ok: boolean | null;
  results: number | null;
  found: Record<string, number> | null;
  missing: string[] | null;
  search_provider?: string | null;
  search_status?: number | null;
  search_detail?: string | null;
  search_fallback?: string | null;
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
  // 取得元による信頼度（high / medium / low / unverified / invalid）
  confidence: string | null;
  confidence_label: string | null;
};

// メールが見つからない時の手動検索導線（公式サイト/Google/LinkedIn/site:）。
export type FallbackSearchQuery = {
  label: string;
  type: string;
  query: string;
  url: string;
};

// 信頼度レベル → 表示ラベル・バッジ色（UI 共通）。
export const EMAIL_CONFIDENCE_LABELS: Record<string, string> = {
  high: "高信頼",
  medium: "要確認",
  low: "低信頼",
  unverified: "未検証",
  invalid: "無効",
};

export const EMAIL_CONFIDENCE_COLORS: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-700",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-orange-100 text-orange-700",
  unverified: "bg-slate-100 text-slate-500",
  invalid: "bg-red-100 text-red-700",
};

// --- Contact Discovery v2（人間の検索手順に近い一本道フロー） ---
export type V2Step = {
  step: number | null;
  phase: string | null; // collect / official_site / crawl / linkedin / extract
  label: string | null;
  status: string | null; // done / empty / running
  detail: string | null;
  urls: string[];
};

export type V2Email = {
  email: string;
  stars: number; // 1〜5（取得元による信頼度）
  confidence_source: string | null; // official_site_contact / footer / about ...
  confidence_label: string | null; // 公式サイト Contact 等
  confidence_level: string | null; // high / medium / low / unverified
  source_url: string | null;
  email_owner: string | null;
  sales_stars: number | null;
  sales_reason: string | null;
  sources: string[];
};

export type V2Candidate = {
  url: string;
  score: number;
  source: string | null; // project_website / search
  adopted: boolean;
  reason: string | null;
  query: string | null;
  title: string | null;
};

export type V2CrawledPage = {
  url: string;
  kind: string | null; // root / contact / about / legal / other
  ok: boolean | null;
  emails: number | null;
};

export type V2LinkedIn = {
  type: string; // company / person
  url: string;
  name: string | null;
  source: string | null;
};

export type ContactDiscovery = {
  id: number;
  project_id: number;
  maker_id: number | null;
  status: DiscoveryStatus;
  sales_contacts: SalesContact[];
  // 営業に使えるメールが無いときの手動検索導線
  fallback_search_queries: FallbackSearchQuery[];
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
  web_search_diagnostics: WebSearchDiagnostic[] | null;
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
  // --- AI Search Agent ---
  search_agent_researched: boolean;
  search_agent_model: string | null;
  search_agent_status: string | null;
  search_agent_steps: SearchAgentStep[] | null;
  search_agent_searched_queries: string[] | null;
  search_agent_searched_urls: string[] | null;
  search_agent_official_site_url: string | null;
  search_agent_emails: DocReaderEmail[] | null;
  search_agent_contact_forms: DocReaderContactForm[] | null;
  search_agent_socials: Record<string, string> | null;
  search_agent_people: DocReaderPerson[] | null;
  search_agent_recommended_channel: string | null;
  search_agent_recommended_contact: string | null;
  search_agent_confidence_score: number | null;
  search_agent_evidence_summary: string | null;
  search_agent_stop_reason: string | null;
  search_agent_error: string | null;
  search_agent_researched_at: string | null;
  // --- Contact Intelligence v3（公式サイト再帰クロール） ---
  recursive_crawl_enabled: boolean;
  recursive_crawled_urls: string[] | null;
  recursive_skipped_urls: string[] | null;
  recursive_emails: DiscoveredEmail[] | null;
  recursive_forms: string[] | null;
  recursive_socials: Record<string, string> | null;
  recursive_pdfs: DiscoveredPdf[] | null;
  recursive_sitemap_urls: string[] | null;
  recursive_robots_sitemaps: string[] | null;
  recursive_has_mx: boolean | null;
  recursive_mx_provider: string | null;
  recursive_spf_record: string | null;
  recursive_dmarc_record: string | null;
  recursive_failure_reasons: string[] | null;
  recursive_summary: string | null;
  recursive_crawled_at: string | null;
  // --- Contact Discovery v2（人間の検索手順に近い一本道フロー） ---
  v2_researched: boolean;
  v2_status: string | null;
  v2_steps: V2Step[] | null;
  v2_company_name: string | null;
  v2_product_name: string | null;
  v2_campaign_url: string | null;
  v2_official_site_url: string | null;
  v2_official_site_source: string | null;
  v2_official_site_candidates: V2Candidate[] | null;
  v2_crawled_pages: V2CrawledPage[] | null;
  v2_emails: V2Email[] | null;
  v2_socials: Record<string, string> | null;
  v2_forms: string[] | null;
  v2_linkedin_company_url: string | null;
  v2_linkedin_person_url: string | null;
  v2_linkedin_candidates: V2LinkedIn[] | null;
  v2_searched_queries: string[] | null;
  v2_search_provider: string | null;
  v2_primary_email: string | null;
  v2_primary_source_url: string | null;
  v2_primary_stars: number | null;
  v2_confidence_score: number | null;
  v2_recommended_channel: string | null;
  v2_summary: string | null;
  v2_error: string | null;
  v2_researched_at: string | null;
  created_at: string;
  updated_at: string;
};

export type ApplyToCrmResult = {
  maker_id: number;
  contact_id: number | null;
  email: string | null;
  recorded: boolean;
};

// ===== Contact Intelligence v5：営業案件管理（SalesOpportunity） =====
export type SalesOpportunityStatus =
  | "not_started"
  | "researched"
  | "contacted"
  | "waiting_reply"
  | "meeting"
  | "negotiating"
  | "likely_contract"
  | "lost"
  | "on_hold";

// 営業案件ステータスの日本語表示。
export const SALES_OPP_STATUS_LABELS: Record<SalesOpportunityStatus, string> = {
  not_started: "未着手",
  researched: "調査済み",
  contacted: "初回連絡済み",
  waiting_reply: "返信待ち",
  meeting: "商談中",
  negotiating: "条件交渉中",
  likely_contract: "契約見込み",
  lost: "失注",
  on_hold: "保留",
};

// ステータス表示色（バッジ用）。
export const SALES_OPP_STATUS_COLORS: Record<SalesOpportunityStatus, string> = {
  not_started: "bg-slate-100 text-slate-600",
  researched: "bg-sky-100 text-sky-700",
  contacted: "bg-indigo-100 text-indigo-700",
  waiting_reply: "bg-amber-100 text-amber-700",
  meeting: "bg-violet-100 text-violet-700",
  negotiating: "bg-fuchsia-100 text-fuchsia-700",
  likely_contract: "bg-emerald-100 text-emerald-700",
  lost: "bg-rose-100 text-rose-600",
  on_hold: "bg-slate-200 text-slate-500",
};

// 優先度の日本語表示。
export const SALES_PRIORITY_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

export type SalesOpportunity = {
  id: number;
  contact_discovery_id: number;
  company_name: string | null;
  product_name: string | null;
  website_url: string | null;
  sales_score: number | null;
  sales_priority: string | null;
  recommended_channel: string | null;
  primary_email: string | null;
  contact_form_url: string | null;
  primary_social_url: string | null;
  status: SalesOpportunityStatus;
  next_action: string | null;
  next_action_due_date: string | null;
  notes: string | null;
  created_at: string;
  updated_at: string;
};

// 営業案件一覧の取得（フィルター・並び替え）。
export async function fetchSalesOpportunities(params: {
  status?: string;
  sales_priority?: string;
  min_score?: number;
  sort?: "score" | "due_date" | "created";
} = {}): Promise<SalesOpportunity[]> {
  const q = new URLSearchParams();
  if (params.status) q.set("status", params.status);
  if (params.sales_priority) q.set("sales_priority", params.sales_priority);
  if (params.min_score != null) q.set("min_score", String(params.min_score));
  if (params.sort) q.set("sort", params.sort);
  const res = await apiFetch(`/sales-opportunities?${q.toString()}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 営業案件の詳細取得。
export async function fetchSalesOpportunity(
  id: number
): Promise<SalesOpportunity> {
  const res = await apiFetch(`/sales-opportunities/${id}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// Contact Intelligence 結果から営業案件を作成（冪等）。
export async function createSalesOpportunityFromDiscovery(
  contactDiscoveryId: number
): Promise<SalesOpportunity> {
  const res = await fetch(
    `${API_BASE}/sales-opportunities/from-contact-discovery/${contactDiscoveryId}`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 営業案件を更新（status / next_action / 期限 / メモ）。
export async function updateSalesOpportunity(
  id: number,
  patch: Partial<
    Pick<
      SalesOpportunity,
      "status" | "next_action" | "next_action_due_date" | "notes"
    >
  >
): Promise<SalesOpportunity> {
  const res = await fetch(`${API_BASE}/sales-opportunities/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

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

// Contact Discovery v2 を実行（同期）。人間の検索手順に近い一本道フロー
// （公式サイト候補探索 → 優先クロール → LinkedIn → メール抽出 → 検証）を実行し、
// 取得元による信頼度（★1〜5）と取得元 URL 付きで返す。失敗時も v2_error を記録して
// 200 で返る。
export async function runContactDiscoveryV2(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(`${API_BASE}/projects/${id}/contact-discovery/v2`, {
    method: "POST",
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
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

// ===== Contact Intelligence v2（非同期ジョブ） =====
export type CIJobType =
  | "web_research"
  | "document_reader"
  | "search_agent"
  | "recursive_crawl"
  | "contact_discovery"
  | "contact_discovery_v2"
  | "ai_research"
  | "full_contact_intelligence";

export type CIJobStatus =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "cancelled";

export type ContactIntelligenceJob = {
  id: number;
  project_id: number;
  job_type: CIJobType | string;
  status: CIJobStatus | string;
  progress: number;
  current_step: string | null;
  logs_json: { ts: string | null; message: string | null }[] | null;
  result_json: Record<string, unknown> | null;
  error: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
  from_cache: boolean;
};

// 重い探索をジョブ化して開始（24hキャッシュ再利用。force で再実行）。すぐ返る。
export async function startContactIntelligenceJob(
  projectId: number,
  jobType: CIJobType = "full_contact_intelligence",
  force = false
): Promise<ContactIntelligenceJob> {
  const res = await fetch(
    `${API_BASE}/projects/${projectId}/contact-intelligence/jobs?job_type=${jobType}&force=${force}`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`API error: ${res.status} ${await res.text()}`);
  return res.json();
}

// ジョブ取得（ポーリング用）。
export async function getContactIntelligenceJob(
  jobId: number
): Promise<ContactIntelligenceJob> {
  const res = await apiFetch(`/contact-intelligence/jobs/${jobId}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 最新ジョブ取得（未実行なら 204 → null）。
export async function getLatestContactIntelligenceJob(
  projectId: number,
  jobType?: CIJobType
): Promise<ContactIntelligenceJob | null> {
  const q = jobType ? `?job_type=${jobType}` : "";
  const res = await apiFetch(
    `/projects/${projectId}/contact-intelligence/jobs/latest${q}`
  );
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ジョブ中断要求。
export async function cancelContactIntelligenceJob(
  jobId: number
): Promise<ContactIntelligenceJob> {
  const res = await fetch(
    `${API_BASE}/contact-intelligence/jobs/${jobId}/cancel`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`API error: ${res.status} ${await res.text()}`);
  return res.json();
}

// AI Search Agent を実行（同期）。次に見るページを判断しながら反復探索。
// Claude 未設定時はモックで動作。失敗時も search_agent_error を記録して 200 で返る。
export async function runSearchAgent(
  id: number
): Promise<ContactDiscovery> {
  const res = await fetch(
    `${API_BASE}/projects/${id}/contact-discovery/search-agent`,
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

// 営業状況フィルター。既定は "not_started"（未営業のみ）。営業アクション済みは除外。
export type RankingStatusFilter =
  | "not_started"
  | "all"
  | "awaiting_reply"
  | "followup"
  | "negotiating";

export const RANKING_STATUS_FILTER_LABELS: Record<RankingStatusFilter, string> = {
  not_started: "未営業のみ",
  all: "すべて表示",
  awaiting_reply: "返事待ち",
  followup: "フォローアップ対象",
  negotiating: "商談中",
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
  // 営業状況フィルター（既定 "not_started"＝未営業のみ）。
  status_filter?: RankingStatusFilter;
  ulule_only?: boolean;
  sort?: RankingSort;
};

// ===== 今日やること（営業アシスタント） =====
// フォロー優先度：normal（3日+）/ high（7日+）/ final（14日+・最終フォロー）
export type FollowUpLevel = "normal" | "high" | "final";

export type SalesTask = {
  project_id: number;
  title: string;
  source_site: string;
  sales_status: SalesStatus;
  latest_score: number | null;
  priority_score: number;
  stars: number;
  has_contact: boolean;
  has_email: boolean;
  days_since_last_outreach: number | null;
  follow_up_level: FollowUpLevel | null;
  reasons: string[];
};

export type TodayTasks = {
  to_contact: SalesTask[];   // 今日営業する案件
  followup: SalesTask[];     // 今日フォローする案件
  replied: SalesTask[];      // 返信あり
  negotiating: SalesTask[];  // 商談中
  idle: SalesTask[];         // 放置でよい案件
};

// トップページ「今日やること」を取得（営業状況で分類した案件リスト）。
export async function fetchSalesTasks(perGroup = 5): Promise<TodayTasks> {
  const res = await apiFetch(`/sales/tasks?per_group=${perGroup}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// ===== 営業 AI コパイロット =====
// 判断カテゴリ（backend の decision と対応）。
export type CopilotDecision =
  | "sell_now"
  | "needs_negotiation"
  | "needs_followup"
  | "needs_email"
  | "needs_contact"
  | "needs_research"
  | "waiting"
  | "data_insufficient"
  | "drop"
  | "closed";

// アクションボタンのキー（backend actions と対応）。
export type CopilotAction =
  | "email"
  | "company_research"
  | "contact_intelligence"
  | "followup"
  | "change_status"
  | "add_crm"
  | "open";

export type CopilotFunding = {
  currency: string;
  raised_amount: number | null;
  goal_amount: number | null;
  backers_count: number | null;
  rate_pct: number | null;
};

export type CopilotSummary = {
  product: string | null;
  company: string | null;
  japan_market_fit: string | null;
  japan_sales_status: string | null;
  funding: CopilotFunding;
  contact_status: string | null;
  contact_person_found: boolean;
  contact_person_name: string | null;
  contact_person_title: string | null;
  contact_person_department: string | null;
  sales_status: SalesStatus;
  last_action: string;
  days_since_last_outreach: number | null;
  next_action: string;
  risks: string[];
  recommendation: { score: number; stars: number; sales_target: string | null };
};

export type CopilotCard = {
  project_id: number;
  title: string;
  source_site: string;
  decision: CopilotDecision;
  decision_label: string;
  next_action: string;
  actions: CopilotAction[];
  reasons: string[];
  priority_score: number;
  stars: number;
  urgency: number;
  recommended_channel: string | null;
  recommended_email: string | null;
  summary: CopilotSummary;
};

export type CopilotDashboard = {
  top_action: CopilotCard | null;
  priority_sales: CopilotCard[];
  needs_contact: CopilotCard[];
  needs_email: CopilotCard[];
  followup: CopilotCard[];
  drop_candidates: CopilotCard[];
  data_insufficient: CopilotCard[];
  counts: Record<string, number>;
  ai_comment: string;
  scanned: number;
};

// 判断カテゴリのバッジ色（UI 共通）。
export const COPILOT_DECISION_COLORS: Record<CopilotDecision, string> = {
  sell_now: "bg-emerald-100 text-emerald-800",
  needs_negotiation: "bg-purple-100 text-purple-800",
  needs_followup: "bg-amber-100 text-amber-800",
  needs_email: "bg-sky-100 text-sky-800",
  needs_contact: "bg-cyan-100 text-cyan-800",
  needs_research: "bg-fuchsia-100 text-fuchsia-800",
  waiting: "bg-slate-100 text-slate-600",
  data_insufficient: "bg-orange-100 text-orange-800",
  drop: "bg-rose-100 text-rose-700",
  closed: "bg-slate-100 text-slate-500",
};

// 営業 AI コパイロット・ダッシュボードを取得（横断判断・バケット分類）。
export async function fetchSalesCopilot(perBucket = 5): Promise<CopilotDashboard> {
  const res = await apiFetch(`/sales/copilot?per_bucket=${perBucket}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// 単一案件の営業コパイロット・カードを取得。
export async function fetchProjectCopilot(id: number): Promise<CopilotCard> {
  const res = await apiFetch(`/projects/${id}/copilot`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// フォローアップメール作成の結果（backend FollowupEmailResult に対応）。
export type FollowupEmailResult = {
  draft: EmailDraft;
  stage: "light" | "repropose" | "final";
  stage_label: string;
  days_since_last_outreach: number;
  follow_up_level: FollowUpLevel;
  gmail_compose_url: string;
  recipient: string | null;
  sales_status: SalesStatus;
};

// フォローアップメール（2通目・3通目）を作成する。経過日数で文面段階が変わる。
export async function createFollowupEmail(
  projectId: number,
  opts?: { days?: number | null; set_awaiting_reply?: boolean; to?: string | null }
): Promise<FollowupEmailResult> {
  const res = await fetch(`${API_BASE}/projects/${projectId}/followup-email`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(opts ?? {}),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
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
  // 既定は「未営業のみ」。営業アクション済みはランキングから除外される。
  qs.set("status_filter", params.status_filter ?? "not_started");
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

// ===== 営業メーカー管理（拡張フィールド・フロント保持） =====
// 既存 Maker / Contact に無い項目（商品名・CF URL・代表者名・LinkedIn 等）も
// 含め、営業対象メーカーを 1 枚のカードで管理するための型・ラベル。
// 現状はフロント（localStorage）保持。将来バックエンド項目化する余地を残す。

// 8 段階の営業ステータス（依頼仕様）。
export type SalesMakerStatus =
  | "not_started"
  | "researching"
  | "email_drafted"
  | "sent"
  | "replied"
  | "meeting"
  | "negotiating"
  | "declined";

export const SALES_MAKER_STATUS_LABELS: Record<SalesMakerStatus, string> = {
  not_started: "未着手",
  researching: "調査中",
  email_drafted: "メール作成済み",
  sent: "送信済み",
  replied: "返信あり",
  meeting: "商談中",
  negotiating: "契約交渉中",
  declined: "見送り",
};

export const SALES_MAKER_STATUS_ORDER: SalesMakerStatus[] = [
  "not_started",
  "researching",
  "email_drafted",
  "sent",
  "replied",
  "meeting",
  "negotiating",
  "declined",
];

export const SALES_MAKER_STATUS_COLORS: Record<SalesMakerStatus, string> = {
  not_started: "bg-slate-100 text-slate-600",
  researching: "bg-sky-100 text-sky-700",
  email_drafted: "bg-indigo-100 text-indigo-700",
  sent: "bg-amber-100 text-amber-700",
  replied: "bg-teal-100 text-teal-700",
  meeting: "bg-violet-100 text-violet-700",
  negotiating: "bg-fuchsia-100 text-fuchsia-700",
  declined: "bg-red-100 text-red-700",
};

// 営業メーカー管理カードで扱う全項目（フロント保持）。
export type SalesMakerProfile = {
  company_name: string;
  product_name: string;
  official_url: string;
  crowdfunding_url: string;
  representative_name: string;
  contact_name: string;
  email: string;
  linkedin_url: string;
  status: SalesMakerStatus;
  next_action: string;
  notes: string;
};

// 空のプロフィール（フォーム初期値）。
export function emptySalesMakerProfile(): SalesMakerProfile {
  return {
    company_name: "",
    product_name: "",
    official_url: "",
    crowdfunding_url: "",
    representative_name: "",
    contact_name: "",
    email: "",
    linkedin_url: "",
    status: "not_started",
    next_action: "",
    notes: "",
  };
}

// モックの AI 営業メール生成（API 未接続時のフォールバック文面）。
// メーカー情報から日本語の初回営業メール案（件名・本文）を組み立てる。
export function buildMockSalesEmail(p: SalesMakerProfile): {
  subject: string;
  body: string;
} {
  const company = p.company_name.trim() || "貴社";
  const product = p.product_name.trim();
  const greetingName = p.contact_name.trim() || p.representative_name.trim();
  const greeting = greetingName
    ? `${greetingName} 様`
    : `${company} ご担当者様`;
  const productLine = product
    ? `貴社の「${product}」を拝見し、その独自性と完成度に大きな可能性を感じております。`
    : "貴社のプロダクトを拝見し、日本市場での大きな可能性を感じております。";
  const refs = [
    p.crowdfunding_url.trim() && `・クラウドファンディング: ${p.crowdfunding_url.trim()}`,
    p.official_url.trim() && `・公式サイト: ${p.official_url.trim()}`,
  ]
    .filter(Boolean)
    .join("\n");

  const subject = product
    ? `【日本市場でのお取り扱いのご相談】${product}について`
    : `【日本市場でのお取り扱いのご相談】${company} 御中`;

  const body = [
    `${greeting}`,
    "",
    "突然のご連絡失礼いたします。",
    "日本にてクラウドファンディング商品の輸入・販売支援を行っております、株式会社〇〇の営業担当でございます。",
    "",
    productLine,
    "つきましては、日本国内での独占的なお取り扱い・販売パートナーシップについて、ぜひ一度ご相談させていただけないでしょうか。",
    refs ? "\n拝見した情報：\n" + refs : "",
    "",
    "・日本語でのマーケティング / カスタマーサポート",
    "・国内クラウドファンディング（Makuake 等）での立ち上げ支援",
    "・国内小売 / EC への販路展開",
    "",
    "上記のようなご支援が可能です。ご興味をお持ちいただけましたら、オンラインにて詳細をご説明させていただきます。",
    "",
    "ご検討のほど、よろしくお願い申し上げます。",
  ]
    .filter((line) => line !== undefined)
    .join("\n");

  return { subject, body };
}

// ===== 営業活動タイムライン（メーカー単位・フロント保持） =====
// 「いつ・何をしたか・次に何をするか」を時系列で管理するための型・ラベル・モック。
// 現状は画面ローカル state（DB 未保存）。

// 活動タイプ（依頼仕様の 8 種）。
export type SalesActivityType =
  | "research"
  | "email_draft"
  | "email_sent"
  | "replied"
  | "followup"
  | "meeting"
  | "negotiation"
  | "note";

export const SALES_ACTIVITY_TYPE_LABELS: Record<SalesActivityType, string> = {
  research: "調査",
  email_draft: "メール作成",
  email_sent: "メール送信",
  replied: "返信あり",
  followup: "フォローアップ",
  meeting: "商談",
  negotiation: "契約交渉",
  note: "メモ",
};

export const SALES_ACTIVITY_TYPE_ORDER: SalesActivityType[] = [
  "research",
  "email_draft",
  "email_sent",
  "replied",
  "followup",
  "meeting",
  "negotiation",
  "note",
];

export const SALES_ACTIVITY_TYPE_COLORS: Record<SalesActivityType, string> = {
  research: "bg-sky-100 text-sky-700",
  email_draft: "bg-indigo-100 text-indigo-700",
  email_sent: "bg-amber-100 text-amber-700",
  replied: "bg-teal-100 text-teal-700",
  followup: "bg-orange-100 text-orange-700",
  meeting: "bg-violet-100 text-violet-700",
  negotiation: "bg-fuchsia-100 text-fuchsia-700",
  note: "bg-slate-100 text-slate-600",
};

// 活動タイプ → 営業ステータスの目安（新規追加時の自動設定に使う）。
export const SALES_ACTIVITY_STATUS_HINT: Record<
  SalesActivityType,
  SalesMakerStatus | null
> = {
  research: "researching",
  email_draft: "email_drafted",
  email_sent: "sent",
  replied: "replied",
  followup: "sent",
  meeting: "meeting",
  negotiation: "negotiating",
  note: null,
};

// 1 件の営業活動（タイムライン項目）。
export type SalesActivity = {
  id: string;
  date: string; // 表示用の日付（YYYY/MM/DD）
  type: SalesActivityType;
  title: string;
  content: string;
  assignee: string | null; // 担当者
  next_action: string | null; // 次回アクション
  status: SalesMakerStatus | null; // その時点の営業ステータス
};

// Date を YYYY/MM/DD 表記へ（タイムラインの日付表示に使う）。
export function formatDateYmd(d: Date = new Date()): string {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}/${m}/${day}`;
}

// 初期表示用のモック営業活動（依頼の例に対応）。
export function mockSalesActivities(): SalesActivity[] {
  return [
    {
      id: "mock-1",
      date: "2026/07/04",
      type: "research",
      title: "公式サイトとクラファンページを確認",
      content:
        "公式サイトとクラウドファンディングページを確認し、商品概要・価格帯・実績を把握。",
      assignee: "自分",
      next_action: "AIで初回営業メールを作成する",
      status: "researching",
    },
    {
      id: "mock-2",
      date: "2026/07/04",
      type: "email_draft",
      title: "AIで初回営業メールを作成",
      content: "AI営業メール生成を利用し、初回営業メールの案を作成。",
      assignee: "自分",
      next_action: "担当者へ初回メールを送信する",
      status: "email_drafted",
    },
    {
      id: "mock-3",
      date: "2026/07/05",
      type: "email_sent",
      title: "担当者へ初回メール送信",
      content: "先方の担当者宛に初回営業メールを送信。開封状況を追う。",
      assignee: "自分",
      next_action: "3営業日返信が無ければフォローアップ",
      status: "sent",
    },
    {
      id: "mock-4",
      date: "2026/07/08",
      type: "followup",
      title: "返信がないため再送予定",
      content: "初回メールに返信が無いため、フォローアップメールの再送を予定。",
      assignee: "自分",
      next_action: "フォローアップメールを送付",
      status: "sent",
    },
  ];
}

// ===== Company Intelligence（メーカーAI分析・フロントモック） =====
// 営業前に必要な会社情報を1枚で確認するためのAI分析結果。
// 今回は実APIを接続せず、buildMockCompanyIntelligence でモック生成する。

export type CompanyIntelligence = {
  company_overview: string; // 会社概要
  representative: string; // 代表者
  location: string; // 所在地
  founded_year: string; // 設立年
  employee_count: string; // 社員数（不明なら空欄 ""）
  main_products: string; // 主力商品
  price_range: string; // 価格帯
  brand_image: string; // ブランドイメージ
  competitors: string; // 競合
  japan_market_fit: string; // 日本市場との相性
  sales_points: string[]; // 営業ポイント
  concerns: string[]; // 懸念点
  recommended_approach: string; // 推奨アプローチ
};

// モックの Company Intelligence を生成する（会社名・商品名があれば反映）。
export function buildMockCompanyIntelligence(seed?: {
  company_name?: string;
  product_name?: string;
}): CompanyIntelligence {
  const company = (seed?.company_name ?? "").trim() || "対象メーカー";
  const product = (seed?.product_name ?? "").trim() || "主力プロダクト";
  return {
    company_overview: `${company} は、独自性の高いプロダクトを展開する海外クラウドファンディング発のメーカー。デザイン性と機能性を両立した製品で、支援者から高い評価を得ている。`,
    representative: "Alex Morgan（CEO / 共同創業者）",
    location: "アメリカ・カリフォルニア州",
    founded_year: "2019年",
    employee_count: "", // 不明のため空欄
    main_products: `${product}、および周辺アクセサリー`,
    price_range: "中〜高価格帯（1万〜3万円相当）",
    brand_image: "革新的・ミニマル・サステナブル志向。若年〜ミドル層の感度の高いユーザーに支持されている。",
    competitors: "大手ガジェットブランド、同カテゴリのクラウドファンディング発ブランド",
    japan_market_fit: "高い。品質重視・デザイン志向の日本市場と親和性が高く、ギフト需要も見込める。",
    sales_points: [
      "日本未発売のため先行独占の余地が大きい",
      "クラウドファンディング実績があり話題性を訴求しやすい",
      "デザイン性が日本の感性に合致",
    ],
    concerns: [
      "技適・PSE 等の国内規制対応の要確認",
      "価格帯がやや高く、初動の販促設計が重要",
      "既存の日本代理店の有無を要確認",
    ],
    recommended_approach:
      "まず英語での初回コンタクトで日本市場のポテンシャルとローカライズ支援を提示。独占販売権と Makuake 等での立ち上げをセットで提案するのが有効。",
  };
}

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

// メーカー作成の結果。created=false は「既存メーカーを再利用した（すでにCRM登録済み）」。
export type MakerRegisterResult = {
  maker: Maker;
  created: boolean;
};

// 海外案件（Project）からメーカーを作成し CRM に登録する（二重登録防止・冪等）。
// 新規作成時は 201、既存メーカー再利用時は 200 が返る。
export async function createMakerFromProject(
  projectId: number
): Promise<MakerRegisterResult> {
  const res = await fetch(`${API_BASE}/crm/makers/from-project/${projectId}`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const maker = (await res.json()) as Maker;
  return { maker, created: res.status === 201 };
}

// 発掘商品（DiscoveredProduct）からメーカーを作成し CRM に登録する（二重登録防止）。
// 新規作成時は 201、既存メーカー再利用時は 200 が返る。
export async function createMakerFromDiscoveredProduct(
  productId: number
): Promise<MakerRegisterResult> {
  const res = await fetch(
    `${API_BASE}/crm/makers/from-discovered-product/${productId}`,
    { method: "POST" }
  );
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  const maker = (await res.json()) as Maker;
  return { maker, created: res.status === 201 };
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

// ===== Discovery Engine（商品発掘）v1-4 =====

// 発掘元プラットフォーム（backend の DiscoverySourcePlatform に対応）
export type DiscoverySourcePlatform =
  | "kickstarter"
  | "indiegogo"
  | "backerkit"
  | "backertracker"
  | "crowdsupply"
  | "gamefound"
  | "producthunt"
  | "manual"
  | "other";

// キャンペーン状態（backend の DiscoveredProductStatus に対応）
export type DiscoveredProductStatus =
  | "live"
  | "successful"
  | "ended"
  | "failed"
  | "canceled"
  | "preorder"
  | "unknown";

// 発掘元の読みやすい表示名。未定義キーはそのまま表示すること。
export const DISCOVERY_PLATFORM_LABELS: Record<string, string> = {
  kickstarter: "Kickstarter",
  indiegogo: "Indiegogo",
  backerkit: "BackerKit",
  backertracker: "BackerTracker",
  crowdsupply: "Crowd Supply",
  gamefound: "Gamefound",
  producthunt: "Product Hunt",
  manual: "手動登録",
  other: "その他",
};

// 手動登録フォーム等で選ばせる発掘元の並び順。
export const DISCOVERY_PLATFORM_ORDER: DiscoverySourcePlatform[] = [
  "kickstarter",
  "indiegogo",
  "backerkit",
  "manual",
  "other",
];

// Discovery 実行で「実サイト取得」に対応済みのプラットフォーム。
// backend の routers/discovery.py `_LIVE_FETCH_PLATFORMS` と必ず一致させること。
// ここに無いものは未接続（準備中）＝実行ボタンを無効化する。
export const DISCOVERY_LIVE_FETCH_PLATFORMS: DiscoverySourcePlatform[] = [
  "kickstarter",
];

// 指定プラットフォームが実サイト取得に対応済みか。
export function isDiscoveryLiveFetch(platform: string): boolean {
  return (DISCOVERY_LIVE_FETCH_PLATFORMS as string[]).includes(platform);
}

// ステータスの日本語表示。
export const DISCOVERY_STATUS_LABELS: Record<string, string> = {
  live: "実施中",
  successful: "成功",
  ended: "終了",
  failed: "未達成",
  canceled: "キャンセル",
  preorder: "予約販売",
  unknown: "不明",
};

export const DISCOVERY_STATUS_COLORS: Record<string, string> = {
  live: "bg-blue-100 text-blue-700",
  successful: "bg-green-100 text-green-700",
  ended: "bg-slate-100 text-slate-600",
  failed: "bg-red-100 text-red-700",
  canceled: "bg-rose-100 text-rose-700",
  preorder: "bg-amber-100 text-amber-700",
  unknown: "bg-slate-100 text-slate-500",
};

export const DISCOVERY_STATUS_ORDER: DiscoveredProductStatus[] = [
  "live",
  "successful",
  "ended",
  "failed",
  "canceled",
  "preorder",
  "unknown",
];

// 発掘商品候補（backend DiscoveredProductOut に対応）。
export type DiscoveredProduct = {
  id: number;
  source_platform: DiscoverySourcePlatform;
  source_url: string | null;
  project_title: string | null;
  creator_name: string | null;
  product_name: string | null;
  category: string | null;
  description: string | null;
  image_url: string | null;
  country: string | null;
  status: DiscoveredProductStatus;
  funding_amount: number | null;
  funding_goal: number | null;
  backers_count: number | null;
  launch_date: string | null;
  end_date: string | null;
  official_website_url: string | null;
  japan_fit_score: number | null;
  crowdfunding_fit_score: number | null;
  novelty_score: number | null;
  logistics_score: number | null;
  regulatory_risk_score: number | null;
  competition_risk_score: number | null;
  japan_entry_risk_score: number | null;
  overall_discovery_score: number | null;
  discovery_reasoning: string | null;
  recommended_next_action: string | null;
  contact_discovery_id: number | null;
  created_at: string;
  updated_at: string;
  // 派生指標（backend の computed field）。未スコアリング/目標額なしは null。
  achievement_rate: number | null; // 達成率（%）
  sales_value_score: number | null; // 営業価値スコア（0〜100）
};

// 商品候補の手動登録ペイロード（backend DiscoveredProductCreate に対応・任意項目）。
export type DiscoveredProductCreate = {
  source_platform?: DiscoverySourcePlatform;
  source_url?: string | null;
  project_title?: string | null;
  creator_name?: string | null;
  product_name?: string | null;
  category?: string | null;
  description?: string | null;
  image_url?: string | null;
  country?: string | null;
  status?: DiscoveredProductStatus;
  funding_amount?: number | null;
  funding_goal?: number | null;
  backers_count?: number | null;
  launch_date?: string | null;
  end_date?: string | null;
  official_website_url?: string | null;
  // true のとき登録直後に自動スコアリング（既定 false）。
  auto_score?: boolean;
};

// 商品候補の更新ペイロード（backend DiscoveredProductUpdate に対応・渡した項目のみ更新）。
export type DiscoveredProductUpdate = Partial<
  Omit<DiscoveredProductCreate, "auto_score">
>;

// Discovery 実行のリクエスト（backend DiscoveryRunRequest に対応）。
export type DiscoveryRunRequest = {
  source_platform: DiscoverySourcePlatform;
  query?: string | null;
  limit?: number;
  auto_score?: boolean;
};

// Discovery 実行の結果サマリ（backend DiscoveryRunResult に対応）。
export type DiscoveryRunResult = {
  run_id: number | null;
  source_platform: string;
  query: string | null;
  status: string;
  found_count: number;
  saved_count: number;
  duplicate_count: number;
  error_message: string | null;
  product_ids: number[];
  started_at: string | null;
  finished_at: string | null;
  // true: 実サイトから取得を試みた（Kickstarter 等）。false: fetch 未接続（0件は仕様）。
  network_fetched: boolean;
  // 取得直後に自動スコアリングできた件数。
  scored_count: number;
};

// 一覧の絞り込み・並び替え。sort は backend の
// "sales"（営業価値）/ "japan"（日本市場適性）/ "score"（総合）/ "created"（新着）に対応。
export type DiscoveryListParams = {
  platform?: string;
  status?: string;
  category?: string;
  min_score?: number;
  sort?: "sales" | "japan" | "score" | "created";
};

// GET /discovery/products
export async function listDiscoveredProducts(
  params: DiscoveryListParams = {}
): Promise<DiscoveredProduct[]> {
  const qs = new URLSearchParams();
  if (params.platform) qs.set("platform", params.platform);
  if (params.status) qs.set("status", params.status);
  if (params.category) qs.set("category", params.category);
  if (params.min_score != null) qs.set("min_score", String(params.min_score));
  if (params.sort) qs.set("sort", params.sort);
  const suffix = qs.toString() ? `?${qs.toString()}` : "";
  const res = await apiFetch(`/discovery/products${suffix}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// GET /discovery/products/{id}
export async function getDiscoveredProduct(
  id: number
): Promise<DiscoveredProduct> {
  const res = await apiFetch(`/discovery/products/${id}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// POST /discovery/products
export async function createDiscoveredProduct(
  payload: DiscoveredProductCreate
): Promise<DiscoveredProduct> {
  const res = await fetch(`${API_BASE}/discovery/products`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// PATCH /discovery/products/{id}
export async function updateDiscoveredProduct(
  id: number,
  payload: DiscoveredProductUpdate
): Promise<DiscoveredProduct> {
  const res = await fetch(`${API_BASE}/discovery/products/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// POST /discovery/products/{id}/score
export async function scoreDiscoveredProduct(
  id: number
): Promise<DiscoveredProduct> {
  const res = await fetch(`${API_BASE}/discovery/products/${id}/score`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// POST /discovery/run
export async function runDiscovery(
  payload: DiscoveryRunRequest
): Promise<DiscoveryRunResult> {
  const res = await fetch(`${API_BASE}/discovery/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// 発掘商品から Contact Intelligence を開始した結果
// （backend DiscoveryContactIntelligenceResult に対応）。
export type DiscoveryContactIntelligenceResult = {
  product_id: number;
  contact_discovery_id: number | null;
  used_url: string | null;
  // started（新規開始）/ existing（既存連携あり）/ error（URL 未設定 等）
  status: string;
  message: string;
};

// POST /discovery/products/{id}/contact-intelligence
export async function startDiscoveryContactIntelligence(
  productId: number
): Promise<DiscoveryContactIntelligenceResult> {
  const res = await fetch(
    `${API_BASE}/discovery/products/${productId}/contact-intelligence`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// ===== Japan Opportunity Engine（日本市場機会 分析）v1-2〜v1-4 =====

// 分析の全スコア軸（backend schemas.japan_opportunity._AnalysisScores に対応）。
// すべて 0〜100（高いほど日本展開/営業に有利）。未評価は null。
export type JapanOpportunityAnalysis = {
  id: number;
  discovered_product_id: number;
  japan_market_fit_score: number | null;
  japan_entry_gap_score: number | null;
  crowdfunding_fit_score: number | null;
  retail_fit_score: number | null;
  regulatory_safety_score: number | null;
  logistics_score: number | null;
  margin_potential_score: number | null;
  competition_gap_score: number | null;
  sales_success_score: number | null;
  overall_opportunity_score: number | null;
  confidence_score: number | null;
  // 根拠テキスト
  japan_presence_summary: string | null;
  competition_summary: string | null;
  regulatory_summary: string | null;
  logistics_summary: string | null;
  pricing_summary: string | null;
  opportunity_reasoning: string | null;
  recommended_strategy: string | null;
  recommended_next_action: string | null;
  // 根拠明細（dict / list を許容）
  evidence_json: unknown | null;
  created_at: string;
  updated_at: string;
};

// GET /japan-opportunity/products/{product_id}/latest
// 発掘商品の最新分析を返す。未分析なら 204 → null。
export async function fetchLatestJapanOpportunity(
  productId: number
): Promise<JapanOpportunityAnalysis | null> {
  const res = await apiFetch(
    `/japan-opportunity/products/${productId}/latest`
  );
  if (res.status === 204) return null;
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// POST /japan-opportunity/analyze/{discovered_product_id}
// ルールベースで評価し、分析を作成して返す（実 AI・実検索なし）。
export async function analyzeJapanOpportunityRules(
  productId: number
): Promise<JapanOpportunityAnalysis> {
  const res = await fetch(
    `${API_BASE}/japan-opportunity/analyze/${productId}`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// POST /japan-opportunity/analyze-ai/{discovered_product_id}
// AI 評価（未接続時はルールベースへフォールバック）で分析を作成して返す。
export async function analyzeJapanOpportunityAi(
  productId: number
): Promise<JapanOpportunityAnalysis> {
  const res = await fetch(
    `${API_BASE}/japan-opportunity/analyze-ai/${productId}`,
    { method: "POST" }
  );
  if (!res.ok) {
    const msg = await res.text();
    throw new Error(`API error: ${res.status} ${msg}`);
  }
  return res.json();
}

// ============================================================================
//  Sales Copilot v2（営業 AI 秘書）
// ============================================================================
export type ScoreGrade = "A" | "B" | "C" | "D" | "E";

export type V2Decision =
  | "sell_now_exclusive"
  | "sell_now"
  | "needs_contact"
  | "needs_email"
  | "needs_research"
  | "needs_followup"
  | "needs_negotiation"
  | "waiting"
  | "deprioritize"
  | "drop"
  | "closed"
  | "data_insufficient"
  | "not_evaluated";

export type AssessmentState =
  | "evaluated"
  | "provisional"
  | "checking_japan"
  | "recompute_pending"
  | "data_insufficient"
  | "not_evaluated"
  | "failed";

export type JapanCheckStatus =
  | "not_checked"
  | "queued"
  | "running"
  | "completed"
  | "failed";

export type JapanCheckResult =
  | "sold_in_japan"
  | "not_found_in_japan"
  | "inconclusive"
  | null;

export interface V2ScoreBlock {
  score: number | null;
  grade: ScoreGrade | null;
  level: string | null;
  reasons: string[];
}

export interface V2Assessment {
  japan_market_fit: V2ScoreBlock;
  exclusivity: V2ScoreBlock;
  makuake_fit: V2ScoreBlock;
  overall_priority_score: number | null;
  overall_grade: ScoreGrade | null;
  confidence: number | null;
  engine: string | null;
  saved: boolean;
  evaluated_at: string | null;
  missing_data: string[] | null;
  state: AssessmentState;
}

export interface V2JapanCheck {
  status: JapanCheckStatus;
  result: JapanCheckResult;
  confidence: number;
  evidence: string[];
  source_urls: string[];
  checked_at: string | null;
  error_reason: string | null;
  version: string | null;
  job_id: number | null;
  job_status: string | null;
}

export interface SalesCopilotV2Card {
  project_id: number;
  title: string;
  source_site: string;
  maker_name: string | null;
  updated_at: string | null;
  funding: {
    currency: string;
    raised_amount: number | null;
    goal_amount: number | null;
    backers_count: number | null;
    rate_pct: number | null;
  } | null;
  decision: V2Decision;
  base_decision: string;
  priority_score: number;
  priority_grade: ScoreGrade | null;
  priority_label: string;
  next_action: string | null;
  reason: string | null;
  tags: string[];
  v1_decision: string;
  v2_decision: string;
  decision_changed: boolean;
  decision_change_reason: string | null;
  v1_decision_label: string | null;
  assessment: V2Assessment;
  japan_sales_check: V2JapanCheck;
  contact: {
    has_email: boolean;
    has_form: boolean;
    recommended_channel: string | null;
    recommended_email: string | null;
  };
  pipeline: {
    sales_status: string | null;
    last_action: string | null;
    contact_person_found: boolean | null;
  };
  actions: string[] | null;
  assessment_state: AssessmentState;
}

export interface SalesCopilotV2Dashboard {
  top_action: SalesCopilotV2Card | null;
  priority_ranking: SalesCopilotV2Card[];
  items: SalesCopilotV2Card[];
  counts: Record<string, number>;
  summary_counts: {
    japan_not_checked: number;
    checking_japan: number;
    data_insufficient: number;
    low_confidence: number;
  };
  scanned: number;
}

// GET /sales/copilot-v2 — v2 ダッシュボード（全カードを items で返す）
export async function fetchSalesCopilotV2(
  perBucket = 5
): Promise<SalesCopilotV2Dashboard> {
  const res = await apiFetch(`/sales/copilot-v2?per_bucket=${perBucket}`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// GET /projects/{id}/copilot-v2 — 単一案件の v2 カード
export async function fetchProjectCopilotV2(
  id: number
): Promise<SalesCopilotV2Card> {
  const res = await apiFetch(`/projects/${id}/copilot-v2`);
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}

// POST /projects/{id}/sales-assessment — 適性を再評価（未実施なら日本チェックを起動）
export async function runSalesAssessment(id: number): Promise<{
  provisional: boolean;
  japan_job_status: string | null;
  japan_state: string | null;
}> {
  const res = await apiFetch(`/projects/${id}/sales-assessment`, {
    method: "POST",
  });
  if (!res.ok) throw new Error(`API error: ${res.status}`);
  return res.json();
}
