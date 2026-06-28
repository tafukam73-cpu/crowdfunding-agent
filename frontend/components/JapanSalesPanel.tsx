"use client";

import { useEffect, useState } from "react";

import {
  CHANNEL_STATUS_LABELS,
  type ChannelFinding,
  type ChannelStatus,
  fetchJapanSalesCheck,
  formatDateTime,
  type JapanSalesCheck,
  runJapanSalesCheck,
} from "@/lib/api";

// 営業価値（★1〜5）。5=日本未販売で最も営業価値が高い。
function Stars({ value }: { value: number | null }) {
  const n = Math.max(0, Math.min(5, value ?? 0));
  return (
    <span className="inline-flex items-center gap-1" aria-label={`営業価値 ${n} / 5`}>
      <span className="text-base leading-none text-amber-500">
        {"★".repeat(n)}
        <span className="text-slate-300">{"★".repeat(5 - n)}</span>
      </span>
      <span className="text-xs font-medium text-slate-500">{n} / 5</span>
    </span>
  );
}

// チャネルの販売/掲載状況バッジ
const STATUS_BADGE: Record<ChannelStatus, string> = {
  found: "bg-rose-100 text-rose-700",
  limited: "bg-amber-100 text-amber-700",
  not_found: "bg-emerald-100 text-emerald-700",
  unknown: "bg-slate-100 text-slate-500",
};

function ChannelRow({ c }: { c: ChannelFinding }) {
  return (
    <li className="flex flex-wrap items-center gap-2 py-1">
      <span className="min-w-[9rem] text-sm font-medium text-slate-700">
        {c.label}
      </span>
      <span
        className={`rounded px-2 py-0.5 text-xs font-medium ${
          STATUS_BADGE[c.status] ?? STATUS_BADGE.unknown
        }`}
      >
        {CHANNEL_STATUS_LABELS[c.status] ?? c.status}
      </span>
      <a
        href={c.search_url}
        target="_blank"
        rel="noopener noreferrer"
        className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
      >
        検索 ↗
      </a>
      {c.note && <span className="text-xs text-slate-400">— {c.note}</span>}
    </li>
  );
}

export default function JapanSalesPanel({ projectId }: { projectId: number }) {
  const [data, setData] = useState<JapanSalesCheck | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchJapanSalesCheck(projectId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      setData(await runJapanSalesCheck(projectId));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const failed = data?.status === "failed";
  const completed = data?.status === "completed";

  return (
    <div className="mt-4 rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          🇯🇵 日本販売状況チェック
        </h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "調査中…" : data ? "再調査" : "日本販売状況を調べる"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        営業前に「既に日本で販売されていないか」を調査し、営業価値（★1〜5）を判定します。
        各チャネルの「検索」から最終確認できます。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {!data && !error && (
        <p className="mt-3 rounded-md border border-dashed border-slate-300 bg-slate-50 p-4 text-sm text-slate-400">
          まだ調査されていません。「日本販売状況を調べる」を押すと、Amazon.co.jp /
          楽天 / Yahoo! / 代理店 / 法人 / Makuake / GREEN FUNDING を評価します。
        </p>
      )}

      {failed && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-semibold">調査に失敗しました。</p>
          <p className="mt-1 whitespace-pre-wrap break-all text-xs">
            {data?.error ?? "原因不明のエラーです。"}
          </p>
          <p className="mt-2 text-xs text-red-500">
            「再調査」で再実行できます（ANTHROPIC_API_KEY 未設定時はモックで動作します）。
          </p>
        </div>
      )}

      {completed && data && (
        <div className="mt-3 space-y-4 text-sm">
          {/* 営業価値 */}
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-xs font-semibold text-slate-500">営業価値</span>
            <Stars value={data.sales_value_stars} />
            {data.summary && (
              <span className="text-xs text-slate-500">{data.summary}</span>
            )}
          </div>

          {/* チャネル別の販売状況 */}
          <div>
            <p className="text-xs font-semibold text-slate-500">チャネル別の販売状況</p>
            <ul className="mt-1 divide-y divide-slate-100">
              {(data.channels ?? []).map((c) => (
                <ChannelRow key={c.channel} c={c} />
              ))}
            </ul>
          </div>

          {/* AIコメント */}
          {data.ai_comment && (
            <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
              <p className="font-semibold">AIコメント</p>
              <p className="mt-1 whitespace-pre-wrap">{data.ai_comment}</p>
            </div>
          )}

          <p className="text-right text-xs text-slate-400">
            {data.model ? `${data.model} ・ ` : ""}
            {formatDateTime(data.updated_at)}
          </p>
        </div>
      )}
    </div>
  );
}
