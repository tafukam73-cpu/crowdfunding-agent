"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchExecutionTasks,
  type ExecutionTaskItem,
  type ExecutionTasks,
} from "@/lib/api";

// ホームの AI 秘書セクション（土台）。
// 送信後ワークフロー（GET /sales/execution-tasks）の事実だけを 3 区分で集計する。
// 提案文の自動生成などは AI 秘書ページ側の役割で、ここでは行わない。
const ROWS: {
  key: string;
  label: string;
  hint: string;
  cls: string;
  pick: (d: ExecutionTasks) => ExecutionTaskItem[];
}[] = [
  {
    key: "today",
    label: "今日やること",
    hint: "今日のフォロー期日・返信対応",
    cls: "bg-rose-50 text-rose-800",
    pick: (d) => [...d.follow_today, ...d.replied],
  },
  {
    key: "awaiting",
    label: "返信待ち",
    hint: "送信済みで相手の返信を待っている",
    cls: "bg-yellow-50 text-yellow-800",
    pick: (d) => d.awaiting_reply,
  },
  {
    key: "followup",
    label: "要フォロー",
    hint: "フォロー期日を過ぎている",
    cls: "bg-amber-50 text-amber-900",
    pick: (d) => d.overdue,
  },
];

export default function HomeAiSecretary({
  reloadKey = 0,
}: {
  reloadKey?: number;
}) {
  const [data, setData] = useState<ExecutionTasks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchExecutionTasks(50)
      .then((d) => {
        if (!active) return;
        setData(d);
        setError(null);
      })
      .catch(() => active && setError("AI秘書の集計を取得できませんでした"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <section className="rounded-lg border border-indigo-200 bg-indigo-50/50 p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-sm font-bold text-indigo-900">🤖 AI秘書</h2>
        <Link
          href="/sales-copilot-v2"
          className="text-xs text-indigo-700 hover:underline"
        >
          AI秘書を開く →
        </Link>
      </div>
      <p className="mt-0.5 text-[11px] text-slate-500">
        送信後の状況を集計して表示します（事実のみ・予測はしません）。
      </p>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {loading && !data && <p className="mt-3 text-sm text-slate-400">読み込み中…</p>}

      {data && (
        <div className="mt-3 space-y-2">
          {ROWS.map((r) => {
            const items = r.pick(data);
            return (
              <div
                key={r.key}
                className={`rounded-md px-3 py-2 ${r.cls}`}
              >
                <div className="flex items-baseline justify-between gap-2">
                  <span className="text-xs font-bold">{r.label}</span>
                  <span className="text-lg font-bold tabular-nums">
                    {items.length}
                  </span>
                </div>
                <p className="text-[11px] opacity-70">{r.hint}</p>
                {items.length > 0 && (
                  <ul className="mt-1 space-y-0.5">
                    {items.slice(0, 2).map((t) => (
                      <li key={`${r.key}-${t.project_id}`} className="truncate">
                        <Link
                          href={`/projects/${t.project_id}?sales=1`}
                          className="text-xs text-blue-700 hover:underline"
                        >
                          {t.title}
                        </Link>
                      </li>
                    ))}
                    {items.length > 2 && (
                      <li className="text-[11px] opacity-60">
                        ほか {items.length - 2} 件
                      </li>
                    )}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}
