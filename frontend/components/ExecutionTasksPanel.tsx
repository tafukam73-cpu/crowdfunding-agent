"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  type ExecutionTaskItem,
  type ExecutionTasks,
  fetchExecutionTasks,
  OUTREACH_LANGUAGE_LABELS,
  REPLY_INTENT_LABELS,
} from "@/lib/api";

function fmtDate(dt: string | null): string {
  if (!dt) return "—";
  try {
    return new Date(dt).toLocaleDateString("ja-JP", {
      month: "2-digit",
      day: "2-digit",
    });
  } catch {
    return dt;
  }
}

function TaskRow({ t, kind }: { t: ExecutionTaskItem; kind: "follow" | "replied" }) {
  return (
    <li className="rounded border border-slate-200 bg-white/80 px-2.5 py-1.5">
      <div className="flex items-start justify-between gap-2">
        <Link
          href={`/projects/${t.project_id}?sales=1`}
          className="truncate text-sm font-medium text-slate-800 hover:text-blue-700 hover:underline"
        >
          {t.title}
        </Link>
        <span className="shrink-0 text-[10px] text-slate-400">
          {OUTREACH_LANGUAGE_LABELS[t.sent_language ?? ""] ?? t.sent_language ?? ""}
        </span>
      </div>
      {kind === "follow" ? (
        <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-slate-500">
          <span>フォロー期日 {fmtDate(t.followup_due_at)}</span>
          {t.days_overdue != null && t.days_overdue > 0 && (
            <span className="rounded bg-rose-100 px-1.5 py-0.5 font-bold text-rose-800">
              {t.days_overdue}日超過
            </span>
          )}
          <span className="text-slate-400">
            フォロー {t.followup_count}/2
          </span>
        </div>
      ) : (
        <div className="mt-0.5 text-[11px] text-slate-500">
          {t.reply_intent && (
            <span className="rounded bg-indigo-100 px-1.5 py-0.5 font-bold text-indigo-800">
              {REPLY_INTENT_LABELS[t.reply_intent] ?? t.reply_intent}
            </span>
          )}
          {t.reply_summary && (
            <span className="ml-1 line-clamp-1">{t.reply_summary}</span>
          )}
        </div>
      )}
    </li>
  );
}

// 送信後の実行タスク（今日フォロー・期限超過・返信対応）を表示する。
// 読み取り専用の GET のみ（Claude・外部 HTTP・状態更新は起こさない）。
export default function ExecutionTasksPanel({ reloadKey }: { reloadKey?: number }) {
  const [data, setData] = useState<ExecutionTasks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchExecutionTasks(50)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  const total =
    data
      ? data.follow_today.length + data.replied.length + data.awaiting_reply.length
      : 0;

  return (
    <div className="rounded-xl border border-blue-200 bg-gradient-to-br from-blue-50 to-white p-5 shadow-sm">
      <h2 className="text-base font-bold text-blue-900">📮 送信後の実行タスク</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        送信済み案件の「今日フォロー・期限超過・返信対応」を自動抽出します（読み取り専用）。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">読み込み失敗：{error}</p>}
      {loading && !data && (
        <p className="mt-2 text-sm text-slate-400">読み込み中…</p>
      )}
      {data && total === 0 && (
        <p className="mt-2 text-sm text-slate-400">
          送信後に対応が必要な案件はありません。
        </p>
      )}

      {data && total > 0 && (
        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-3">
          <div className="rounded-lg border border-amber-300 bg-amber-50 p-3">
            <p className="text-xs font-bold text-slate-700">
              ② 今日フォロー
              <span className="ml-1 font-normal text-slate-400">
                {data.follow_today.length}
              </span>
              {data.overdue.length > 0 && (
                <span className="ml-1 rounded bg-rose-200 px-1.5 py-0.5 text-[10px] font-bold text-rose-900">
                  期限超過 {data.overdue.length}
                </span>
              )}
            </p>
            {data.follow_today.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">なし</p>
            ) : (
              <ul className="mt-1.5 space-y-1.5">
                {data.follow_today.map((t) => (
                  <TaskRow key={t.project_id} t={t} kind="follow" />
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-indigo-300 bg-indigo-50 p-3">
            <p className="text-xs font-bold text-slate-700">
              ③ 返信対応
              <span className="ml-1 font-normal text-slate-400">
                {data.replied.length}
              </span>
            </p>
            {data.replied.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">なし</p>
            ) : (
              <ul className="mt-1.5 space-y-1.5">
                {data.replied.map((t) => (
                  <TaskRow key={t.project_id} t={t} kind="replied" />
                ))}
              </ul>
            )}
          </div>

          <div className="rounded-lg border border-slate-300 bg-white p-3">
            <p className="text-xs font-bold text-slate-700">
              返信待ち
              <span className="ml-1 font-normal text-slate-400">
                {data.awaiting_reply.length}
              </span>
            </p>
            {data.awaiting_reply.length === 0 ? (
              <p className="mt-2 text-xs text-slate-400">なし</p>
            ) : (
              <ul className="mt-1.5 space-y-1.5">
                {data.awaiting_reply.map((t) => (
                  <TaskRow key={t.project_id} t={t} kind="follow" />
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
