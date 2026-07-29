"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchTodayPriority, type TodayPriorityItem } from "@/lib/api";

const ACTION_LABELS: Record<string, string> = {
  open_draft: "下書きを開く",
  generate_email: "メール生成",
  find_contact: "連絡先探索",
};

const ACTION_COLORS: Record<string, string> = {
  open_draft: "bg-emerald-100 text-emerald-700",
  generate_email: "bg-indigo-100 text-indigo-700",
  find_contact: "bg-amber-100 text-amber-700",
};

// 優先案件 TOP5。営業実行パイプラインの優先度順（既存 GET /sales/today-priority）
// から上位 5 件だけを見せ、詳細は「今日のタスク」へ送る。
export default function HomePriorityTop5({
  reloadKey = 0,
}: {
  reloadKey?: number;
}) {
  const [items, setItems] = useState<TodayPriorityItem[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchTodayPriority(5)
      .then((d) => {
        if (!active) return;
        setItems(d.slice(0, 5));
        setError(null);
      })
      .catch(() => {
        if (!active) return;
        setItems([]);
        setError("優先案件を取得できませんでした");
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-bold text-slate-800">🎯 優先案件 TOP5</h2>
        <Link href="/tasks" className="text-xs text-blue-700 hover:underline">
          すべて見る →
        </Link>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {loading && <p className="mt-3 text-sm text-slate-400">読み込み中…</p>}
      {!loading && items && items.length === 0 && !error && (
        <p className="mt-3 text-sm text-slate-500">
          今日営業できる（連絡先探索・評価が済んだ）案件はありません。
        </p>
      )}

      <ol className="mt-3 space-y-1.5">
        {items?.map((p, i) => (
          <li
            key={p.project_id}
            className="flex flex-wrap items-center gap-2 rounded-md border border-slate-100 px-2.5 py-2 hover:bg-slate-50"
          >
            <span className="w-4 shrink-0 text-center text-xs font-bold text-slate-400">
              {i + 1}
            </span>
            <span className="shrink-0 rounded bg-emerald-600 px-1.5 py-0.5 text-[11px] font-bold text-white tabular-nums">
              {p.score}
            </span>
            <Link
              href={`/projects/${p.project_id}?sales=1`}
              className="min-w-0 flex-1 truncate text-sm font-medium text-blue-700 hover:underline"
            >
              {p.title}
            </Link>
            {!p.contact_ready && (
              <span className="shrink-0 text-[10px] text-amber-600">連絡先未取得</span>
            )}
            <span
              className={`shrink-0 rounded px-2 py-0.5 text-[11px] font-medium ${
                ACTION_COLORS[p.recommended_action] ?? "bg-slate-100 text-slate-600"
              }`}
            >
              {ACTION_LABELS[p.recommended_action] ?? p.recommended_action}
            </span>
          </li>
        ))}
      </ol>
    </section>
  );
}
