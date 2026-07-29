"use client";

import { useEffect, useState } from "react";

import SalesStatusBadge from "@/components/SalesStatusBadge";
import {
  CHANGE_SOURCE_LABELS,
  fetchStatusEvents,
  formatDateTime,
  type SalesStatusEvent,
} from "@/lib/api";

// 営業履歴。営業状況（sales_status）の変更履歴を新しい順に並べる。
// 既存の GET /projects/{id}/status-events のみを読む（状態は変更しない）。
export default function ProjectHistoryPanel({
  projectId,
  refreshKey = 0,
}: {
  projectId: number;
  refreshKey?: number;
}) {
  const [events, setEvents] = useState<SalesStatusEvent[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchStatusEvents(projectId, 100)
      .then((d) => {
        if (!active) return;
        setEvents(d);
        setError(null);
      })
      .catch(() => active && setError("営業履歴を取得できませんでした"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [projectId, refreshKey]);

  if (loading) return <p className="text-sm text-slate-400">読み込み中…</p>;
  if (error) return <p className="text-sm text-red-600">{error}</p>;
  if (!events || events.length === 0) {
    return (
      <p className="text-sm text-slate-500">
        営業状況の変更履歴はまだありません。営業を開始すると、ここに記録されます。
      </p>
    );
  }

  return (
    <ol className="space-y-2">
      {events.map((e) => (
        <li
          key={e.id}
          className="flex flex-wrap items-center gap-2 rounded-md border border-slate-100 bg-slate-50 px-3 py-2"
        >
          <span className="shrink-0 text-xs tabular-nums text-slate-500">
            {formatDateTime(e.created_at)}
          </span>
          {e.from_status && (
            <>
              <SalesStatusBadge status={e.from_status} />
              <span className="text-xs text-slate-400">→</span>
            </>
          )}
          <SalesStatusBadge status={e.to_status} />
          <span className="rounded bg-white px-1.5 py-0.5 text-[10px] text-slate-500">
            {CHANGE_SOURCE_LABELS[e.change_source] ?? e.change_source}
          </span>
          {e.note && (
            <span className="w-full text-xs text-slate-600">{e.note}</span>
          )}
        </li>
      ))}
    </ol>
  );
}
