"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import SalesStatusBadge from "@/components/SalesStatusBadge";
import { fetchProjects, type Project } from "@/lib/api";

/** 「3時間前」のような相対表記。1 週間を超えたら日付で出す。 */
function relativeTime(iso: string | null): string {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diffMin = Math.floor((Date.now() - t) / 60000);
  if (diffMin < 1) return "たった今";
  if (diffMin < 60) return `${diffMin}分前`;
  const diffH = Math.floor(diffMin / 60);
  if (diffH < 24) return `${diffH}時間前`;
  const diffD = Math.floor(diffH / 24);
  if (diffD <= 7) return `${diffD}日前`;
  return new Date(iso).toLocaleDateString("ja-JP");
}

// 最近更新された案件。既存の一覧 API を更新日降順で 5 件だけ引く。
export default function HomeRecentProjects({
  reloadKey = 0,
}: {
  reloadKey?: number;
}) {
  const [items, setItems] = useState<Project[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchProjects({ sort: "updated_at", order: "desc", page: 1, page_size: 5 })
      .then((d) => {
        if (!active) return;
        setItems(d.items);
        setError(null);
      })
      .catch(() => {
        if (!active) return;
        setItems([]);
        setError("案件を取得できませんでした");
      })
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-bold text-slate-800">🕒 最近更新された案件</h2>
        <Link
          href="/projects?sort=updated_at"
          className="text-xs text-blue-700 hover:underline"
        >
          一覧を見る →
        </Link>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {loading && <p className="mt-3 text-sm text-slate-400">読み込み中…</p>}
      {!loading && items && items.length === 0 && !error && (
        <p className="mt-3 text-sm text-slate-500">案件がまだありません。</p>
      )}

      <ul className="mt-3 space-y-1.5">
        {items?.map((p) => (
          <li
            key={p.id}
            className="flex flex-wrap items-center gap-2 rounded-md border border-slate-100 px-2.5 py-2 hover:bg-slate-50"
          >
            <Link
              href={`/projects/${p.id}`}
              className="min-w-0 flex-1 truncate text-sm font-medium text-blue-700 hover:underline"
            >
              {p.title}
            </Link>
            <SalesStatusBadge status={p.sales_status} />
            <span className="shrink-0 text-[11px] text-slate-400">
              {relativeTime(p.updated_at)}
            </span>
          </li>
        ))}
      </ul>
    </section>
  );
}
