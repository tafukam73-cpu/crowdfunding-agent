"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

import Header from "@/components/Header";
import {
  createMaker,
  CRM_STATUS_COLORS,
  CRM_STATUS_LABELS,
  fetchMakers,
  fetchReminders,
  formatDateTime,
  type CrmStatus,
  type MakerList,
  type Reminder,
} from "@/lib/api";

const STATUSES: CrmStatus[] = ["lead", "contacted", "negotiating", "won", "lost"];
const PAGE_SIZE = 20;

export default function CrmPage() {
  const [data, setData] = useState<MakerList | null>(null);
  const [reminders, setReminders] = useState<Reminder[]>([]);
  const [error, setError] = useState<string | null>(null);

  const [status, setStatus] = useState<CrmStatus | "">("");
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [newName, setNewName] = useState("");
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    fetchMakers({ status, q, page, page_size: PAGE_SIZE })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e)));
    fetchReminders(30)
      .then(setReminders)
      .catch(() => setReminders([]));
  }, [status, q, page]);

  useEffect(() => {
    load();
  }, [load]);

  async function onCreate() {
    if (!newName.trim()) return;
    setCreating(true);
    try {
      await createMaker({ name: newName.trim(), status: "lead" });
      setNewName("");
      setPage(1);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setCreating(false);
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <h1 className="text-xl font-bold">営業管理（CRM）</h1>

        {/* リマインダー */}
        <div className="mt-6 rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">
            リマインダー（30日以内・期限切れ）
          </h2>
          {reminders.length === 0 ? (
            <p className="mt-2 text-sm text-slate-400">予定はありません。</p>
          ) : (
            <ul className="mt-3 space-y-1.5">
              {reminders.map((r) => (
                <li key={r.maker_id} className="flex items-center gap-3 text-sm">
                  <span
                    className={`w-20 shrink-0 rounded px-2 py-0.5 text-center text-xs ${
                      r.overdue
                        ? "bg-red-100 text-red-700"
                        : "bg-blue-100 text-blue-700"
                    }`}
                  >
                    {r.overdue ? "期限切れ" : "予定"}
                  </span>
                  <span className="w-24 shrink-0 text-slate-500">
                    {r.next_action_date}
                  </span>
                  <Link
                    href={`/crm/makers/${r.maker_id}`}
                    className="font-medium text-blue-700 hover:underline"
                  >
                    {r.maker_name}
                  </Link>
                  <span className="truncate text-slate-600">
                    {r.next_action ?? "—"}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>

        {/* 新規メーカー */}
        <div className="mt-6 flex items-end gap-3">
          <label className="flex flex-1 flex-col text-xs text-slate-500">
            新規メーカー名
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="会社名"
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && onCreate()}
            />
          </label>
          <button
            onClick={onCreate}
            disabled={creating || !newName.trim()}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            追加
          </button>
        </div>

        {/* フィルタ */}
        <div className="mt-6 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs text-slate-500">
            ステータス
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={status}
              onChange={(e) => {
                setPage(1);
                setStatus(e.target.value as CrmStatus | "");
              }}
            >
              <option value="">すべて</option>
              {STATUSES.map((s) => (
                <option key={s} value={s}>
                  {CRM_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-1 flex-col text-xs text-slate-500">
            メーカー名検索
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

        {error && <p className="mt-4 text-red-600">読み込み失敗：{error}</p>}

        {/* 一覧 */}
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2">メーカー</th>
                <th className="px-4 py-2">ステータス</th>
                <th className="px-4 py-2">次回アクション</th>
                <th className="px-4 py-2">期日</th>
                <th className="px-4 py-2">更新日時</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((m) => (
                <tr key={m.id} className="border-t border-slate-100 hover:bg-slate-50">
                  <td className="px-4 py-3">
                    <Link
                      href={`/crm/makers/${m.id}`}
                      className="font-medium text-blue-700 hover:underline"
                    >
                      {m.name}
                    </Link>
                    {m.country && (
                      <div className="text-xs text-slate-400">{m.country}</div>
                    )}
                  </td>
                  <td className="px-4 py-3">
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${CRM_STATUS_COLORS[m.status]}`}
                    >
                      {CRM_STATUS_LABELS[m.status]}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-slate-600">{m.next_action ?? "—"}</td>
                  <td className="px-4 py-3 text-slate-600">
                    {m.next_action_date ?? "—"}
                  </td>
                  <td className="px-4 py-3 text-xs text-slate-500">
                    {formatDateTime(m.updated_at)}
                  </td>
                </tr>
              ))}
              {data?.items.length === 0 && (
                <tr>
                  <td colSpan={5} className="px-4 py-8 text-center text-slate-400">
                    メーカーがありません
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ページング */}
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>{data ? `全 ${data.total} 件` : ""}</span>
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
