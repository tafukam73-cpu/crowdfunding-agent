"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchSalesTasks,
  SALES_STATUS_LABELS,
  type SalesTask,
  type TodayTasks,
} from "@/lib/api";

// 「今日やること」の 4 グループ（営業フロー順）
const GROUPS: {
  key: keyof TodayTasks;
  num: string;
  label: string;
  cls: string;
}[] = [
  { key: "to_contact", num: "①", label: "営業", cls: "border-emerald-300 bg-emerald-50" },
  { key: "followup", num: "②", label: "フォローアップ", cls: "border-amber-300 bg-amber-50" },
  { key: "replied", num: "③", label: "返信あり", cls: "border-indigo-300 bg-indigo-50" },
  { key: "negotiating", num: "④", label: "商談中", cls: "border-purple-300 bg-purple-50" },
];

function TaskRow({ task, contact }: { task: SalesTask; contact: boolean }) {
  // 「営業」グループは営業フローを開始（?sales=1）、それ以外は案件詳細へ
  const href = contact
    ? `/projects/${task.project_id}?sales=1`
    : `/projects/${task.project_id}`;
  return (
    <li>
      <Link
        href={href}
        className="flex items-center justify-between gap-2 rounded px-2 py-1 text-sm hover:bg-white/70"
      >
        <span className="truncate font-medium text-slate-800">{task.title}</span>
        <span className="shrink-0 text-[10px] text-slate-400">
          {SALES_STATUS_LABELS[task.sales_status]}
        </span>
      </Link>
    </li>
  );
}

export default function TodayTasksPanel({ reloadKey }: { reloadKey?: number }) {
  const [data, setData] = useState<TodayTasks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchSalesTasks(5)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <div className="rounded-xl border border-rose-200 bg-gradient-to-br from-rose-50 to-white p-5 shadow-sm">
      <h2 className="text-base font-bold text-rose-900">🔥 今日やること</h2>

      {error && <p className="mt-2 text-sm text-red-600">読み込み失敗：{error}</p>}
      {loading && !data && (
        <p className="mt-2 text-sm text-slate-400">読み込み中…</p>
      )}

      {data && (
        <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {GROUPS.map((g) => {
            const items = data[g.key];
            return (
              <div key={g.key} className={`rounded-lg border p-3 ${g.cls}`}>
                <p className="text-xs font-bold text-slate-700">
                  {g.num} {g.label}
                  <span className="ml-1 font-normal text-slate-400">
                    {items.length}
                  </span>
                </p>
                {items.length === 0 ? (
                  <p className="mt-2 text-xs text-slate-400">なし</p>
                ) : (
                  <ul className="mt-1.5 space-y-0.5">
                    {items.map((t) => (
                      <TaskRow
                        key={t.project_id}
                        task={t}
                        contact={g.key === "to_contact"}
                      />
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
