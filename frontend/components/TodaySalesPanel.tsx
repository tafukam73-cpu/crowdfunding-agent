"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchSalesDashboard,
  fetchTodayProjects,
  SALES_STATUS_LABELS,
  type SalesDashboard,
  type TodayProject,
} from "@/lib/api";

function Stars({ n }: { n: number }) {
  const full = Math.max(0, Math.min(5, n));
  return (
    <span className="text-amber-500" aria-label={`優先度 ${full} / 5`}>
      {"★".repeat(full)}
      <span className="text-slate-300">{"☆".repeat(5 - full)}</span>
    </span>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-md border border-slate-200 bg-white px-3 py-2 text-center">
      <p className="text-lg font-bold text-slate-900">{value}</p>
      <p className="text-[11px] text-slate-500">{label}</p>
    </div>
  );
}

// 「今日営業する案件」＋営業ダッシュボード。トップページ最上部に表示し、
// 優先順位の高い（準備完了・未営業）案件から営業に着手できるようにする。
export default function TodaySalesPanel({
  reloadKey = 0,
}: {
  reloadKey?: number;
}) {
  const [items, setItems] = useState<TodayProject[] | null>(null);
  const [dash, setDash] = useState<SalesDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchTodayProjects(5)
      .then(setItems)
      .catch((e) => setError(String(e)));
    fetchSalesDashboard()
      .then(setDash)
      .catch(() => setDash(null));
  }, [reloadKey]);

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/60 p-5">
      <h2 className="text-sm font-bold text-indigo-900">今日営業する案件</h2>

      {/* 営業ダッシュボード */}
      {dash && (
        <div className="mt-3 grid grid-cols-3 gap-2 sm:grid-cols-5">
          <Stat label="営業準備完了" value={dash.ready_count} />
          <Stat label="今日営業する件数" value={dash.today_count} />
          <Stat label="返信待ち" value={dash.awaiting_reply_count} />
          <Stat label="商談中" value={dash.negotiating_count} />
          <Stat label="契約数" value={dash.won_count} />
        </div>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {/* 優先順位順の案件 */}
      <div className="mt-4 space-y-2">
        {items && items.length === 0 && (
          <p className="text-sm text-slate-500">
            今日営業すべき準備完了の案件はありません。企業リサーチ・連絡先探索・営業メール生成を進めてください。
          </p>
        )}
        {items?.map((p) => (
          <div
            key={p.project_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2"
          >
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <Stars n={p.stars} />
                <Link
                  href={`/projects/${p.project_id}`}
                  className="truncate font-medium text-blue-700 hover:underline"
                >
                  {p.title}
                </Link>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                  {SALES_STATUS_LABELS[p.sales_status]}
                </span>
              </div>
              {p.reasons.length > 0 && (
                <p className="mt-0.5 text-xs text-slate-500">
                  {p.reasons.map((r) => `・${r}`).join("　")}
                </p>
              )}
            </div>
            <span className="text-xs text-slate-400">優先度 {p.priority_score}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
