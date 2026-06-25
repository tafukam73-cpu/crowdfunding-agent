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
  created_at: string;
  updated_at: string;
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
  created_at: string;
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

export const SCRAPE_STATUS_LABELS: Record<ScrapeStatus, string> = {
  running: "実行中",
  success: "成功",
  error: "失敗",
};

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
