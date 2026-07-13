"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchTodayPriority,
  OUTREACH_LANGUAGE_LABELS,
  OUTREACH_STATUS_LABELS,
  type TodayPriorityItem,
} from "@/lib/api";

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

// 営業実行パイプラインの「今日営業する案件」。優先度順（日本市場適性/独占/Makuake/
// email/maker/新規）で並べ、Contact Intelligence 完了案件のみを表示する。
export default function TodayPriorityPanel({
  reloadKey = 0,
}: {
  reloadKey?: number;
}) {
  const [items, setItems] = useState<TodayPriorityItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchTodayPriority(8)
      .then((d) => active && setItems(d))
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
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/60 p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-bold text-emerald-900">
          🎯 今日営業する案件（優先度順）
        </h2>
        <span className="text-[11px] text-emerald-700/70">
          Discovery→Promote→Contact Intelligence→営業実行
        </span>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      <div className="mt-3 space-y-2">
        {loading && <p className="text-sm text-slate-400">読み込み中…</p>}
        {!loading && items && items.length === 0 && !error && (
          <p className="text-sm text-slate-500">
            今日営業できる（Contact Intelligence 完了・評価済み）案件はありません。
          </p>
        )}
        {items?.map((p) => (
          <div
            key={p.project_id}
            className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-slate-200 bg-white px-3 py-2"
          >
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="rounded bg-emerald-600 px-1.5 py-0.5 text-[11px] font-bold text-white">
                  {p.score}
                </span>
                <Link
                  href={`/projects/${p.project_id}`}
                  className="truncate font-medium text-blue-700 hover:underline"
                >
                  {p.title}
                </Link>
                <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
                  {OUTREACH_LANGUAGE_LABELS[p.recommended_language] ??
                    p.recommended_language}
                </span>
                {p.outreach_status && (
                  <span className="rounded bg-blue-100 px-1.5 py-0.5 text-[10px] text-blue-700">
                    {OUTREACH_STATUS_LABELS[p.outreach_status]}
                  </span>
                )}
              </div>
              {p.reasons.length > 0 && (
                <p className="mt-0.5 truncate text-xs text-slate-500">
                  {p.reasons.map((r) => `・${r}`).join("　")}
                </p>
              )}
            </div>
            <div className="flex items-center gap-2">
              {!p.contact_ready && (
                <span className="text-[10px] text-amber-600">連絡先未取得</span>
              )}
              <span
                className={`rounded px-2 py-0.5 text-[11px] font-medium ${
                  ACTION_COLORS[p.recommended_action] ??
                  "bg-slate-100 text-slate-600"
                }`}
              >
                {ACTION_LABELS[p.recommended_action] ?? p.recommended_action}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
