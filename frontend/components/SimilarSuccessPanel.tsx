"use client";

import { useEffect, useState } from "react";

import {
  fetchSimilarJapanese,
  formatMoney,
  SITE_LABELS,
  type SimilarSuccess,
  type SourceSite,
} from "@/lib/api";

function rate(s: SimilarSuccess): number | null {
  if (!s.goal_amount || !s.raised_amount) return null;
  return Math.round((s.raised_amount / s.goal_amount) * 100);
}

function platformLabel(platform: string): string {
  return SITE_LABELS[platform as SourceSite] ?? platform;
}

function SuccessCard({ item }: { item: SimilarSuccess }) {
  const r = rate(item);
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="rounded bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
              {platformLabel(item.platform)}
            </span>
            {item.category && (
              <span className="text-xs text-slate-400">{item.category}</span>
            )}
          </div>
          <p className="mt-1 font-medium text-slate-800">{item.title}</p>
        </div>
        <div className="shrink-0 text-right">
          <div className="text-xl font-bold text-slate-900">
            {item.match_score}
          </div>
          <div className="text-[10px] text-slate-400">類似度</div>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-xs text-slate-600">
        <span>応援購入額：{formatMoney(item.raised_amount, item.currency)}</span>
        {r != null && <span>達成率：{r.toLocaleString()}%</span>}
        {item.backers_count != null && (
          <span>支援者：{item.backers_count.toLocaleString()}人</span>
        )}
      </div>

      {item.match_reasons.length > 0 && (
        <ul className="mt-3 space-y-1 text-xs text-slate-600">
          {item.match_reasons.map((reason, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="text-slate-400">・</span>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
      )}

      {item.source_url && (
        <a
          href={item.source_url}
          target="_blank"
          rel="noreferrer"
          className="mt-3 inline-block text-xs text-blue-700 hover:underline"
        >
          Makuake で見る ↗
        </a>
      )}
    </div>
  );
}

export default function SimilarSuccessPanel({
  projectId,
}: {
  projectId: number;
}) {
  const [items, setItems] = useState<SimilarSuccess[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    setLoading(true);
    fetchSimilarJapanese(projectId)
      .then(setItems)
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  return (
    <div className="mt-8">
      <h2 className="text-sm font-semibold text-slate-700">
        類似する日本の成功事例
      </h2>
      <p className="mt-1 text-xs text-slate-400">
        ※ Makuake 等の応援購入成功案件から、カテゴリ・実績をもとに自動抽出しています。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <div className="mt-3 space-y-3">
        {loading && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            読み込み中…
          </p>
        )}
        {!loading && items.length === 0 && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            類似する日本の成功事例は見つかりませんでした。
          </p>
        )}
        {!loading &&
          items.map((item) => <SuccessCard key={item.id} item={item} />)}
      </div>
    </div>
  );
}
