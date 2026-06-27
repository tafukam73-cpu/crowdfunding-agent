"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import SourceBadge from "@/components/SourceBadge";
import {
  fetchScrapeRuns,
  formatDateTime,
  runScrape,
  SCRAPE_STATUS_COLORS,
  SCRAPE_STATUS_LABELS,
  type ScrapeRun,
} from "@/lib/api";

export default function ScrapePanel({ onCompleted }: { onCompleted?: () => void }) {
  const [runs, setRuns] = useState<ScrapeRun[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const prevRunning = useRef(false);

  const load = useCallback(async () => {
    try {
      const data = await fetchScrapeRuns(8);
      setRuns(data);
      const anyRunning = data.some((r) => r.status === "running");
      // running → 完了に変わったら一覧をリフレッシュ
      if (prevRunning.current && !anyRunning) {
        onCompleted?.();
      }
      prevRunning.current = anyRunning;
      setBusy(anyRunning);
    } catch (e) {
      setError(String(e));
    }
  }, [onCompleted]);

  // 3秒ごとにポーリング
  useEffect(() => {
    load();
    timer.current = setInterval(load, 3000);
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, [load]);

  async function onRun() {
    setError(null);
    setBusy(true);
    try {
      await runScrape(undefined, 10); // 海外営業対象サイト・各最大10件
      prevRunning.current = true;
      await load();
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">海外案件収集</h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "収集中…" : "収集実行（海外サイト）"}
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <div className="mt-3">
        <p className="text-xs text-slate-400">最新の収集結果</p>
        <ul className="mt-2 space-y-1">
          {runs.length === 0 && (
            <li className="text-sm text-slate-400">収集履歴はまだありません</li>
          )}
          {runs.map((r) => (
            <li
              key={r.id}
              className="flex flex-wrap items-center gap-2 text-sm text-slate-600"
            >
              <SourceBadge site={r.site} />
              <span
                className={`rounded-full px-2 py-0.5 text-xs ${SCRAPE_STATUS_COLORS[r.status]}`}
              >
                {SCRAPE_STATUS_LABELS[r.status]}
              </span>
              {r.status !== "running" && (
                <span className="text-xs text-slate-500">
                  取得 {r.fetched_count} / 新規 {r.created_count} / 更新 {r.updated_count}
                </span>
              )}
              {r.error && (
                <span className="text-xs text-red-500" title={r.error}>
                  {r.error.slice(0, 60)}
                </span>
              )}
              <span className="ml-auto text-xs text-slate-400">
                {formatDateTime(r.finished_at ?? r.started_at)}
              </span>
            </li>
          ))}
        </ul>
      </div>
    </section>
  );
}
