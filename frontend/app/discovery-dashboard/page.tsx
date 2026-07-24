"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";

import Header from "@/components/Header";
import {
  analyzeJapanOpportunityAi,
  analyzeJapanOpportunityRules,
  DISCOVERY_PLATFORM_LABELS,
  DISCOVERY_PLATFORM_ORDER,
  DISCOVERY_STATUS_COLORS,
  DISCOVERY_STATUS_LABELS,
  DISCOVERY_STATUS_ORDER,
  fetchLatestJapanOpportunity,
  listDiscoveredProducts,
  startDiscoveryContactIntelligence,
  type DiscoveredProduct,
  type DiscoveryListParams,
  type JapanOpportunityAnalysis,
} from "@/lib/api";

// ---------------------------------------------------------------------------
// 表示ヘルパー
// ---------------------------------------------------------------------------

// 発掘元の読みやすい表示（未定義キーはそのまま）。
function platformLabel(p: string): string {
  return DISCOVERY_PLATFORM_LABELS[p] ?? p;
}

// スコアは 0〜100 / 未評価 で表示する。
function scoreText(n: number | null | undefined): string {
  return n == null ? "未評価" : `${n}/100`;
}

// 80以上は高評価として目立たせ、60未満は控えめ表示にする。
function scoreColor(n: number | null | undefined): string {
  if (n == null) return "bg-slate-100 text-slate-400";
  if (n >= 80) return "bg-green-100 text-green-700 font-semibold";
  if (n >= 60) return "bg-amber-100 text-amber-700";
  return "bg-slate-100 text-slate-400";
}

// 「TypeError: Failed to fetch」等の生エラーをそのまま出さず、日本語で安全に説明する。
function friendlyError(e: unknown): string {
  const msg = e instanceof Error ? e.message : String(e);
  if (/failed to fetch|networkerror|load failed|network request/i.test(msg)) {
    return "サーバーに接続できませんでした。バックエンドの起動状態やネットワークをご確認ください。";
  }
  if (msg.includes("タイムアウト")) return msg;
  if (/API error: 5\d\d/.test(msg)) {
    return "サーバー内部でエラーが発生しました。時間をおいて再度お試しください。";
  }
  if (/API error: 404/.test(msg)) {
    return "対象が見つかりませんでした（削除済みの可能性があります）。";
  }
  return `処理に失敗しました：${msg}`;
}

function money(n: number | null): string {
  return n == null ? "—" : Number(n).toLocaleString();
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        DISCOVERY_STATUS_COLORS[status] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {DISCOVERY_STATUS_LABELS[status] ?? status}
    </span>
  );
}

// スコア 1 項目のチップ。
function ScoreChip({
  label,
  value,
}: {
  label: string;
  value: number | null | undefined;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs ${scoreColor(
        value
      )}`}
      title={label}
    >
      <span className="opacity-70">{label}</span>
      <span>{scoreText(value)}</span>
    </span>
  );
}

// 発掘商品の代表スコア（並び替え・集計用）。
function opportunityScore(a: JapanOpportunityAnalysis | null): number | null {
  return a?.overall_opportunity_score ?? null;
}

// ---------------------------------------------------------------------------
// 型
// ---------------------------------------------------------------------------
type Row = {
  product: DiscoveredProduct;
  analysis: JapanOpportunityAnalysis | null; // null = 未分析
};

type SortUi = "opportunity_desc" | "discovery_desc" | "created_desc";

// ---------------------------------------------------------------------------
// A. サマリーカード
// ---------------------------------------------------------------------------
function SummaryCard({
  label,
  value,
  accent,
}: {
  label: string;
  value: number;
  accent?: "green" | "sky" | "amber";
}) {
  const color =
    accent === "green"
      ? "text-green-700"
      : accent === "sky"
      ? "text-sky-700"
      : accent === "amber"
      ? "text-amber-700"
      : "text-slate-900";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${color}`}>{value}</p>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ランキングカード（C. 表示 + D. 操作）
// ---------------------------------------------------------------------------
function RankingCard({
  rank,
  row,
  onAnalysisChanged,
  onProductChanged,
}: {
  rank: number;
  row: Row;
  onAnalysisChanged: (productId: number, a: JapanOpportunityAnalysis) => void;
  onProductChanged: (p: DiscoveredProduct) => void;
}) {
  const { product, analysis } = row;
  const [busy, setBusy] = useState<null | "rules" | "ai" | "ci">(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function runRules() {
    setBusy("rules");
    setError(null);
    setMessage(null);
    try {
      const a = await analyzeJapanOpportunityRules(product.id);
      onAnalysisChanged(product.id, a);
      setMessage("ルール分析を実行しました。");
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  async function runAi() {
    setBusy("ai");
    setError(null);
    setMessage(null);
    try {
      const a = await analyzeJapanOpportunityAi(product.id);
      onAnalysisChanged(product.id, a);
      setMessage("AI分析を実行しました。");
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  async function startCi() {
    setBusy("ci");
    setError(null);
    setMessage(null);
    try {
      const res = await startDiscoveryContactIntelligence(product.id);
      onProductChanged({
        ...product,
        contact_discovery_id: res.contact_discovery_id,
      });
      setMessage(
        `${res.message}（Contact ID: ${res.contact_discovery_id ?? "—"}）`
      );
    } catch (e) {
      setError(friendlyError(e));
    } finally {
      setBusy(null);
    }
  }

  const title = product.product_name || product.project_title || "(名称未設定)";
  const hasUrl = Boolean(product.official_website_url || product.source_url);
  const linked = product.contact_discovery_id != null;
  const oppScore = opportunityScore(analysis);
  const isHigh =
    (product.overall_discovery_score ?? 0) >= 80 || (oppScore ?? 0) >= 80;

  return (
    <div
      className={`rounded-lg border bg-white p-4 ${
        isHigh ? "border-green-300 ring-1 ring-green-100" : "border-slate-200"
      }`}
    >
      {/* ヘッダー行 */}
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <span className="mt-0.5 shrink-0 rounded bg-slate-900 px-2 py-0.5 text-xs font-bold text-white">
            #{rank}
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-sm font-bold text-slate-900">
              {title}
            </h3>
            {product.project_title &&
              product.project_title !== product.product_name && (
                <p className="truncate text-xs text-slate-500">
                  {product.project_title}
                </p>
              )}
          </div>
        </div>
        <StatusBadge status={product.status} />
      </div>

      {/* メタ情報 */}
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600">
        <span>発掘元: {platformLabel(product.source_platform)}</span>
        {product.category && <span>カテゴリ: {product.category}</span>}
        {product.country && <span>国: {product.country}</span>}
        <span>調達額: {money(product.funding_amount)}</span>
        <span>支援者: {product.backers_count ?? "—"}</span>
      </div>

      {/* 総合スコア（Discovery / Japan Opportunity / Confidence） */}
      <div className="mt-3 flex flex-wrap gap-1.5">
      </div>

      {/* 予測スコア（営業成功可能性・利益率・法規制安全性 等）は表示しない。
          AI が書いた推薦理由は「AI要約」と明示する。 */}
      {analysis?.opportunity_reasoning && (
        <p className="mt-2 line-clamp-3 text-xs text-slate-600">
          <span className="mr-1 rounded bg-violet-100 px-1.5 py-0.5 text-[10px] font-bold text-violet-700">
            AI要約
          </span>
          {analysis.opportunity_reasoning}
        </p>
      )}
      {analysis?.recommended_strategy && (
        <p className="mt-1 line-clamp-2 text-xs text-slate-600">
          <span className="font-medium text-slate-700">推奨戦略:</span>{" "}
          {analysis.recommended_strategy}
        </p>
      )}
      {(analysis?.recommended_next_action ||
        product.recommended_next_action) && (
        <p className="mt-1 line-clamp-2 text-xs text-slate-600">
          <span className="font-medium text-slate-700">推奨次アクション:</span>{" "}
          {analysis?.recommended_next_action ??
            product.recommended_next_action}
        </p>
      )}

      {product.source_url && (
        <a
          href={product.source_url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-xs text-blue-600 hover:underline"
        >
          元ページを開く ↗
        </a>
      )}

      {/* Contact Intelligence 連携状態 */}
      <div className="mt-2 text-xs">
        {linked ? (
          <span className="inline-flex items-center rounded bg-green-100 px-2 py-0.5 font-medium text-green-700">
            Contact Intelligence 連携済み（ID: {product.contact_discovery_id}）
          </span>
        ) : (
          <span className="inline-flex items-center rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-500">
            Contact Intelligence 未連携
          </span>
        )}
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {message && <p className="mt-2 text-xs text-green-700">{message}</p>}

      {/* D. 操作ボタン */}
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={runRules}
          disabled={busy !== null}
          className="rounded bg-slate-900 px-3 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy === "rules" ? "分析中…" : "ルール分析"}
        </button>
        <button
          onClick={runAi}
          disabled={busy !== null}
          className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        >
          {busy === "ai" ? "分析中…" : "AI分析"}
        </button>
        {!linked && (
          <button
            onClick={startCi}
            disabled={busy !== null || !hasUrl}
            title={
              hasUrl
                ? undefined
                : "official_website_url も source_url も未設定です"
            }
            className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy === "ci" ? "開始中…" : "Contact Intelligence開始"}
          </button>
        )}
        {!hasUrl && !linked && (
          <span className="text-xs text-amber-600">URL未設定</span>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ページ本体
// ---------------------------------------------------------------------------
export default function DiscoveryDashboardPage() {
  const [products, setProducts] = useState<DiscoveredProduct[]>([]);
  // productId -> 最新分析（null = 未分析）。未取得の productId はキー無し。
  const [analyses, setAnalyses] = useState<
    Record<number, JapanOpportunityAnalysis | null>
  >({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // C. フィルター
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [minScore, setMinScore] = useState("");
  const [sortUi, setSortUi] = useState<SortUi>("opportunity_desc");
  const [onlyHighScore, setOnlyHighScore] = useState(false);
  const [onlyUnlinked, setOnlyUnlinked] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: DiscoveryListParams = { sort: "score" };
      if (platform) params.platform = platform;
      if (status) params.status = status;
      const list = await listDiscoveredProducts(params);
      setProducts(list);

      // 各商品の最新 Japan Opportunity 分析を並行取得（1件失敗しても全体は壊さない）。
      const entries = await Promise.all(
        list.map(async (p) => {
          try {
            const a = await fetchLatestJapanOpportunity(p.id);
            return [p.id, a] as const;
          } catch {
            return [p.id, null] as const;
          }
        })
      );
      const map: Record<number, JapanOpportunityAnalysis | null> = {};
      for (const [id, a] of entries) map[id] = a;
      setAnalyses(map);
    } catch (e) {
      setError(friendlyError(e));
      setProducts([]);
      setAnalyses({});
    } finally {
      setLoading(false);
    }
  }, [platform, status]);

  useEffect(() => {
    load();
  }, [load]);

  function onAnalysisChanged(productId: number, a: JapanOpportunityAnalysis) {
    setAnalyses((prev) => ({ ...prev, [productId]: a }));
  }

  function onProductChanged(updated: DiscoveredProduct) {
    setProducts((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p))
    );
  }

  // 商品と分析を結合した行。
  const rows: Row[] = useMemo(
    () =>
      products.map((product) => ({
        product,
        analysis: analyses[product.id] ?? null,
      })),
    [products, analyses]
  );

  // A. サマリー集計。
  const summary = useMemo(() => {
    const total = rows.length;
    const unanalyzed = rows.filter((r) => r.analysis == null).length;
    const highScore = rows.filter((r) => {
      const opp = opportunityScore(r.analysis) ?? 0;
      const disc = r.product.overall_discovery_score ?? 0;
      return opp >= 80 || disc >= 80;
    }).length;
    const linked = rows.filter(
      (r) => r.product.contact_discovery_id != null
    ).length;
    // 営業候補：Japan Opportunity 分析済みで総合機会スコア 60 以上。
    const salesCandidates = rows.filter(
      (r) => (opportunityScore(r.analysis) ?? 0) >= 60
    ).length;
    return { total, unanalyzed, highScore, linked, salesCandidates };
  }, [rows]);

  // C. フィルター + 並び替えを適用した表示行。
  const visibleRows: Row[] = useMemo(() => {
    const min = minScore === "" ? null : Number(minScore);
    let out = rows.filter((r) => {
      const opp = opportunityScore(r.analysis);
      const disc = r.product.overall_discovery_score;
      if (min != null) {
        // 機会スコア優先、なければ Discovery 総合で判定。
        const eff = opp ?? disc ?? 0;
        if (eff < min) return false;
      }
      if (onlyHighScore) {
        if ((opp ?? 0) < 80 && (disc ?? 0) < 80) return false;
      }
      if (onlyUnlinked && r.product.contact_discovery_id != null) return false;
      return true;
    });

    const byScoreDesc = (get: (r: Row) => number | null) => (a: Row, b: Row) => {
      const av = get(a);
      const bv = get(b);
      // null は常に後ろ。
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return bv - av;
    };

    out = [...out];
    if (sortUi === "opportunity_desc") {
      out.sort(byScoreDesc((r) => opportunityScore(r.analysis)));
    } else if (sortUi === "discovery_desc") {
      out.sort(byScoreDesc((r) => r.product.overall_discovery_score));
    } else {
      // created_desc
      out.sort(
        (a, b) =>
          new Date(b.product.created_at).getTime() -
          new Date(a.product.created_at).getTime()
      );
    }
    return out;
  }, [rows, minScore, onlyHighScore, onlyUnlinked, sortUi]);

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-5xl space-y-6 px-6 py-6">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              AI商品発掘ランキング / Discovery Dashboard
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              発掘した商品を営業判断しやすいランキングで表示します。スコアは 0〜100
              （80以上が高評価）。
            </p>
          </div>
          <Link
            href="/discovery"
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-100"
          >
            商品発掘へ移動 →
          </Link>
        </div>

        {/* A. サマリーカード */}
        <section className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
          <SummaryCard label="登録商品数" value={summary.total} />
          <SummaryCard
            label="未評価（未分析）"
            value={summary.unanalyzed}
            accent="amber"
          />
          <SummaryCard
            label="高スコア商品（80以上）"
            value={summary.highScore}
            accent="green"
          />
          <SummaryCard
            label="Contact Intelligence連携済み"
            value={summary.linked}
            accent="sky"
          />
          <SummaryCard label="営業候補（機会60以上）" value={summary.salesCandidates} />
        </section>

        {/* C. フィルター */}
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="mb-3 text-base font-bold text-slate-900">
            フィルター・並び替え
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <label className="text-xs text-slate-600">
              発掘元
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">すべて</option>
                {DISCOVERY_PLATFORM_ORDER.map((p) => (
                  <option key={p} value={p}>
                    {platformLabel(p)}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-600">
              ステータス
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">すべて</option>
                {DISCOVERY_STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {DISCOVERY_STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-600">
              最低スコア
              <input
                type="number"
                min={0}
                max={100}
                value={minScore}
                onChange={(e) => setMinScore(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="0〜100"
              />
            </label>
            <label className="text-xs text-slate-600">
              並び替え
              <select
                value={sortUi}
                onChange={(e) => setSortUi(e.target.value as SortUi)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="opportunity_desc">
                  Japan Opportunity が高い順
                </option>
                <option value="discovery_desc">Discovery総合が高い順</option>
                <option value="created_desc">登録が新しい順</option>
              </select>
            </label>
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-4">
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={onlyHighScore}
                onChange={(e) => setOnlyHighScore(e.target.checked)}
              />
              高スコアのみ（80以上）
            </label>
            <label className="flex items-center gap-2 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={onlyUnlinked}
                onChange={(e) => setOnlyUnlinked(e.target.checked)}
              />
              Contact Intelligence 未連携のみ
            </label>
            <button
              onClick={load}
              disabled={loading}
              className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {loading ? "読み込み中…" : "再読み込み"}
            </button>
            <span className="text-xs text-slate-500">
              {visibleRows.length} / {rows.length} 件
            </span>
          </div>

          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </section>

        {/* B. ランキング一覧 */}
        {!loading && rows.length === 0 && !error && (
          <p className="text-sm text-slate-500">
            発掘商品がまだありません。
            <Link href="/discovery" className="text-blue-600 hover:underline">
              商品発掘
            </Link>
            から登録・実行してください。
          </p>
        )}
        {!loading && rows.length > 0 && visibleRows.length === 0 && (
          <p className="text-sm text-slate-500">
            フィルター条件に一致する商品がありません。
          </p>
        )}

        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {visibleRows.map((row, i) => (
            <RankingCard
              key={row.product.id}
              rank={i + 1}
              row={row}
              onAnalysisChanged={onAnalysisChanged}
              onProductChanged={onProductChanged}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
