"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { fetchSalesTasks, type SalesTask, type TodayTasks } from "@/lib/api";

// 「今日やること」を営業フロー順に 4 区分で要約する。詳細な実行リストは
// /tasks（今日のタスク）にあり、ホームでは件数と最初の一手だけを見せる。
const BUCKETS: {
  key: keyof TodayTasks;
  label: string;
  cta: string;
  cls: string;
}[] = [
  {
    key: "to_contact",
    label: "営業する",
    cta: "営業メール作成",
    cls: "border-emerald-300 bg-emerald-50 text-emerald-900",
  },
  {
    key: "followup",
    label: "フォローする",
    cta: "フォローアップ",
    cls: "border-amber-300 bg-amber-50 text-amber-900",
  },
  {
    key: "replied",
    label: "返信に対応",
    cta: "返信を確認",
    cls: "border-indigo-300 bg-indigo-50 text-indigo-900",
  },
  {
    key: "needs_contact",
    label: "連絡先を探す",
    cta: "連絡先を探索",
    cls: "border-sky-300 bg-sky-50 text-sky-900",
  },
];

function todayLabel(): string {
  const d = new Date();
  const w = ["日", "月", "火", "水", "木", "金", "土"][d.getDay()];
  return `${d.getMonth() + 1}月${d.getDate()}日（${w}）`;
}

export default function HomeTodayCard({ reloadKey = 0 }: { reloadKey?: number }) {
  const [data, setData] = useState<TodayTasks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchSalesTasks(3)
      .then((d) => {
        if (!active) return;
        setData(d);
        setError(null);
      })
      .catch(() => active && setError("今日やることを取得できませんでした"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const counts = BUCKETS.map((b) => ({
    ...b,
    items: (data?.[b.key] ?? []) as SalesTask[],
  }));
  const total = counts.reduce((n, b) => n + b.items.length, 0);

  // 「次の一手」：区分の優先順（営業→フォロー→返信→連絡先）で先頭 3 件を拾う。
  const nextUp = counts
    .flatMap((b) => b.items.map((t) => ({ task: t, bucket: b })))
    .slice(0, 3);

  return (
    <section className="rounded-xl border border-rose-200 bg-gradient-to-br from-rose-50 to-white p-5 shadow-sm">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-base font-bold text-rose-900">🔥 今日やること</h2>
        <span className="text-xs text-slate-500">{todayLabel()}</span>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
      {loading && !data && <p className="mt-3 text-sm text-slate-400">読み込み中…</p>}

      {data && (
        <>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
            {counts.map((b) => (
              <Link
                key={String(b.key)}
                href="/tasks"
                className={`rounded-lg border px-3 py-2 transition hover:brightness-95 ${b.cls}`}
              >
                <p className="text-2xl font-bold tabular-nums">{b.items.length}</p>
                <p className="text-xs">{b.label}</p>
              </Link>
            ))}
          </div>

          {total === 0 ? (
            <p className="mt-4 text-sm text-slate-500">
              今日対応が必要な案件はありません。新しい案件の連絡先探索を進めましょう。
            </p>
          ) : (
            <ol className="mt-4 space-y-1.5">
              {nextUp.map(({ task, bucket }, i) => (
                <li
                  key={`${String(bucket.key)}-${task.project_id}`}
                  className="flex flex-wrap items-center gap-2 rounded-md border border-slate-200 bg-white px-3 py-2"
                >
                  <span className="text-xs font-bold text-slate-400">{i + 1}</span>
                  <Link
                    href={`/projects/${task.project_id}?sales=1`}
                    className="min-w-0 flex-1 truncate text-sm font-medium text-blue-700 hover:underline"
                  >
                    {task.title}
                  </Link>
                  <span className="shrink-0 rounded bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600">
                    {bucket.cta}
                  </span>
                </li>
              ))}
            </ol>
          )}

          <div className="mt-3 text-right">
            <Link
              href="/tasks"
              className="text-xs font-medium text-rose-700 hover:underline"
            >
              今日のタスクをすべて見る →
            </Link>
          </div>
        </>
      )}
    </section>
  );
}
