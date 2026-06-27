"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import CostPanel from "@/components/CostPanel";
import Header from "@/components/Header";
import RecBadge from "@/components/RecBadge";
import ScheduleStatusPanel from "@/components/ScheduleStatusPanel";
import ScrapePanel from "@/components/ScrapePanel";
import ScrapeStatsPanel from "@/components/ScrapeStatsPanel";
import SourceBadge from "@/components/SourceBadge";
import StatusBadge from "@/components/StatusBadge";
import {
  AVAILABILITY_COLORS,
  AVAILABILITY_LABELS,
  evaluateRun,
  fetchEvaluateEstimate,
  fetchProjects,
  fundingRate,
  formatDateTime,
  formatMoney,
  SALES_TARGET_SITES,
  SITE_LABELS,
  STATUS_LABELS,
  type ListParams,
  type ProjectList,
  type ProjectStatus,
  type Recommendation,
  type SourceSite,
} from "@/lib/api";

const PAGE_SIZE = 10;

export default function Home() {
  const [data, setData] = useState<ProjectList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [site, setSite] = useState<SourceSite | "">("");
  const [status, setStatus] = useState<ProjectStatus | "">("");
  const [q, setQ] = useState("");
  const [recommendation, setRecommendation] = useState<Recommendation | "">("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [costKey, setCostKey] = useState(0);
  const [evaluating, setEvaluating] = useState(false);

  useEffect(() => {
    const params: ListParams = {
      site,
      status,
      q,
      recommendation,
      sort,
      order,
      page,
      page_size: PAGE_SIZE,
    };
    setLoading(true);
    fetchProjects(params)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [site, status, q, recommendation, sort, order, page, reloadKey]);

  async function onEvaluateAll() {
    try {
      // 実行前に推定コストを表示して確認
      const est = await fetchEvaluateEstimate();
      if (est.count === 0) {
        window.alert("未評価の案件はありません。");
        return;
      }
      const msg =
        est.mode === "claude"
          ? `未評価 ${est.count} 件を ${est.model} で評価します。\n` +
            `推定: 入力 ${est.est_input_tokens.toLocaleString()} tok / ` +
            `出力 ${est.est_output_tokens.toLocaleString()} tok / ` +
            `約 $${est.est_cost_usd.toFixed(4)}\n実行しますか？`
          : `未評価 ${est.count} 件をモック評価します（コスト $0）。実行しますか？`;
      if (!window.confirm(msg)) return;
    } catch (e) {
      setError(String(e));
      return;
    }

    setEvaluating(true);
    try {
      await evaluateRun();
      // バックグラウンド評価。少し待ってから一覧・コストを再読込
      setTimeout(() => {
        setReloadKey((k) => k + 1);
        setCostKey((k) => k + 1);
      }, 1500);
    } catch (e) {
      setError(String(e));
    } finally {
      setTimeout(() => setEvaluating(false), 1500);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        {/* 収集コントロール */}
        <ScrapePanel onCompleted={() => setReloadKey((k) => k + 1)} />

        {/* 自動収集（日次スケジューラ）の状況 */}
        <div className="mt-6">
          <ScheduleStatusPanel onCompleted={() => setReloadKey((k) => k + 1)} />
        </div>

        {/* 取得モニタリング（成功率・構造変化・403 等） */}
        <div className="mt-6">
          <ScrapeStatsPanel />
        </div>

        {/* AI 利用コスト */}
        <CostPanel reloadKey={costKey} />

        <div className="mt-8 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">海外営業対象案件</h1>
            <p className="mt-1 text-sm text-slate-500">
              Kickstarter / Indiegogo / Wadiz の案件のみ。日本の成功事例（Makuake /
              GreenFunding）は
              <Link href="/japanese-success" className="text-blue-700 hover:underline">
                日本の成功事例
              </Link>
              で確認できます。
            </p>
          </div>
          <button
            onClick={onEvaluateAll}
            disabled={evaluating}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm font-medium text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {evaluating ? "評価中…" : "未評価をAI評価"}
          </button>
        </div>

        {/* フィルタ */}
        <div className="mt-4 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs text-slate-500">
            サイト
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={site}
              onChange={(e) => {
                setPage(1);
                setSite(e.target.value as SourceSite | "");
              }}
            >
              <option value="">すべて</option>
              {SALES_TARGET_SITES.map((v) => (
                <option key={v} value={v}>
                  {SITE_LABELS[v]}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            ステータス
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={status}
              onChange={(e) => {
                setPage(1);
                setStatus(e.target.value as ProjectStatus | "");
              }}
            >
              <option value="">すべて</option>
              {Object.entries(STATUS_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            推奨度
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={recommendation}
              onChange={(e) => {
                setPage(1);
                setRecommendation(e.target.value as Recommendation | "");
              }}
            >
              <option value="">すべて</option>
              <option value="high">高</option>
              <option value="mid">中</option>
              <option value="low">低</option>
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            並び替え
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="created_at">登録日</option>
              <option value="latest_score">AI総合スコア</option>
              <option value="raised_amount">調達額</option>
              <option value="backers_count">支援者数</option>
              <option value="end_date">終了日</option>
              <option value="title">案件名</option>
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            順序
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={order}
              onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
            >
              <option value="desc">降順</option>
              <option value="asc">昇順</option>
            </select>
          </label>

          <label className="flex flex-1 flex-col text-xs text-slate-500">
            案件名検索
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="キーワード"
              value={q}
              onChange={(e) => {
                setPage(1);
                setQ(e.target.value);
              }}
            />
          </label>
        </div>

        {error && (
          <p className="mt-6 text-red-600">
            読み込み失敗：{error}（バックエンド http://localhost:8000 を確認）
          </p>
        )}

        {/* 一覧テーブル */}
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2">案件名</th>
                <th className="px-4 py-2">サイト</th>
                <th className="px-4 py-2">スコア</th>
                <th className="px-4 py-2">推奨度</th>
                <th className="px-4 py-2">日本判定</th>
                <th className="px-4 py-2">調達額</th>
                <th className="px-4 py-2">達成率</th>
                <th className="px-4 py-2">支援者</th>
                <th className="px-4 py-2">ステータス</th>
                <th className="px-4 py-2">取得日時</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => {
                const rate = fundingRate(p);
                return (
                  <tr key={p.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      <Link
                        href={`/projects/${p.id}`}
                        className="font-medium text-blue-700 hover:underline"
                      >
                        {p.title}
                      </Link>
                      <div className="text-xs text-slate-400">{p.category ?? "—"}</div>
                    </td>
                    <td className="px-4 py-3">
                      <SourceBadge site={p.source_site} />
                    </td>
                    <td className="px-4 py-3">
                      {p.latest_score != null ? (
                        <span className="font-semibold text-slate-800">
                          {p.latest_score}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <RecBadge recommendation={p.latest_recommendation} />
                    </td>
                    <td className="px-4 py-3">
                      {p.latest_availability ? (
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-medium ${AVAILABILITY_COLORS[p.latest_availability]}`}
                        >
                          {AVAILABILITY_LABELS[p.latest_availability]}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      {formatMoney(p.raised_amount, p.currency)}
                    </td>
                    <td className="px-4 py-3">{rate != null ? `${rate}%` : "—"}</td>
                    <td className="px-4 py-3">
                      {p.backers_count?.toLocaleString() ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={p.status} />
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {formatDateTime(p.updated_at)}
                    </td>
                  </tr>
                );
              })}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={10} className="px-4 py-8 text-center text-slate-400">
                    該当する案件がありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ページング */}
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>
            {data ? `全 ${data.total} 件` : ""}
            {loading && "（読み込み中…）"}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              前へ
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </button>
          </div>
        </div>
      </main>
    </>
  );
}
