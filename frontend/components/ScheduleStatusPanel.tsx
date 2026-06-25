"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchScheduleStatus,
  formatDateTime,
  runAllScrape,
  SCRAPE_STATUS_COLORS,
  SCRAPE_STATUS_LABELS,
  SITE_LABELS,
  type ScheduleStatus,
} from "@/lib/api";

export default function ScheduleStatusPanel({
  onCompleted,
}: {
  onCompleted?: () => void;
}) {
  const [status, setStatus] = useState<ScheduleStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(() => {
    fetchScheduleStatus()
      .then(setStatus)
      .catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onRunAll() {
    setRunning(true);
    setError(null);
    try {
      await runAllScrape();
      // バックグラウンド収集。少し待ってから状態を再読込
      setTimeout(() => {
        load();
        onCompleted?.();
        setRunning(false);
      }, 2500);
    } catch (e) {
      setError(String(e));
      setRunning(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">自動収集の状況</h2>
        <button
          onClick={onRunAll}
          disabled={running}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {running ? "収集中…" : "今すぐ実行"}
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {status && (
        <p className="mt-2 text-xs text-slate-500">
          スケジュール：
          <span className="font-medium text-slate-700">
            {status.enabled ? "有効" : "無効"}
          </span>
          {" ・ "}cron <code className="text-slate-700">{status.cron}</code> (
          {status.timezone})
          {" ・ "}次回 {formatDateTime(status.next_run_time)}
        </p>
      )}

      <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
        {status?.sites.map(({ site, last_run }) => (
          <div
            key={site}
            className="flex items-center justify-between rounded border border-slate-100 bg-slate-50 px-3 py-2 text-sm"
          >
            <span className="font-medium text-slate-700">{SITE_LABELS[site]}</span>
            <span className="flex items-center gap-2 text-xs text-slate-500">
              {last_run ? (
                <>
                  <span
                    className={`rounded px-2 py-0.5 font-medium ${SCRAPE_STATUS_COLORS[last_run.status]}`}
                  >
                    {SCRAPE_STATUS_LABELS[last_run.status]}
                  </span>
                  {last_run.status === "success" && (
                    <span>
                      新{last_run.created_count}/更{last_run.updated_count}
                    </span>
                  )}
                  <span>{formatDateTime(last_run.finished_at ?? last_run.started_at)}</span>
                </>
              ) : (
                <span className="text-slate-400">未実行</span>
              )}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
