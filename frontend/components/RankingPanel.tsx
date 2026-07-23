"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import CrmRegisterButton from "@/components/CrmRegisterButton";
import SourceBadge from "@/components/SourceBadge";
import {
  EXECUTIVE_CHANNEL_LABELS,
  fetchSalesRanking,
  RANKING_SORT_LABELS,
  RANKING_STATUS_FILTER_LABELS,
  type RankingItem,
  type RankingSort,
  type RankingStatusFilter,
  SALES_TARGET_SITES,
  type SalesTarget,
  SITE_LABELS,
  type SourceSite,
} from "@/lib/api";

// 営業対象（YES / NO / 要確認）の色分け
const TARGET_STYLE: Record<SalesTarget, { label: string; cls: string }> = {
  yes: { label: "営業対象 YES", cls: "bg-emerald-600 text-white" },
  no: { label: "営業対象 NO", cls: "bg-rose-600 text-white" },
  "要確認": { label: "営業対象 要確認", cls: "bg-amber-500 text-white" },
};

// 推奨アクションの強調色（「今すぐ営業」を最も目立たせる）
const ACTION_STYLE: Record<string, string> = {
  今すぐ営業: "bg-emerald-100 text-emerald-800",
  連絡先探索が必要: "bg-sky-100 text-sky-800",
  日本販売状況を確認: "bg-amber-100 text-amber-800",
  営業対象外の可能性: "bg-rose-100 text-rose-800",
  後回し: "bg-slate-100 text-slate-700",
};

function Stars({ value }: { value: number }) {
  const n = Math.max(0, Math.min(5, value));
  return (
    <span className="text-amber-500" aria-label={`星評価 ${n} / 5`}>
      {"★".repeat(n)}
      <span className="text-slate-300">{"★".repeat(5 - n)}</span>
    </span>
  );
}

function RankCard({ item }: { item: RankingItem }) {
  const target = TARGET_STYLE[item.sales_target];
  const actionCls = ACTION_STYLE[item.recommended_action] ?? "bg-slate-100 text-slate-700";
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="text-lg font-extrabold text-indigo-700">#{item.rank}</span>
        <Link
          href={`/projects/${item.project_id}`}
          className="text-base font-bold text-slate-900 hover:text-blue-700 hover:underline"
        >
          {item.title}
        </Link>
        <SourceBadge site={item.source_site as SourceSite} />
        <span className={`rounded-full px-2.5 py-0.5 text-xs font-bold ${target.cls}`}>
          {target.label}
        </span>
      </div>

      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
        <span>
          <Stars value={item.stars} />{" "}
          <span className="font-bold text-slate-800">{item.score}点</span>
        </span>
        <span className={`rounded px-2 py-0.5 text-xs font-bold ${actionCls}`}>
          {item.recommended_action}
        </span>
        <span className="text-xs text-slate-500">
          推奨：
          {EXECUTIVE_CHANNEL_LABELS[item.recommended_channel] ??
            item.recommended_channel}
        </span>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-4 gap-y-0.5 text-xs text-slate-600">
        <span>日本販売：{item.japan_sales_status}</span>
        <span>{item.japan_distributor_status}</span>
        <span>連絡先：{item.contact_status}</span>
      </div>

      {item.reasons.length > 0 && (
        <ul className="mt-2 space-y-0.5 text-xs text-slate-700">
          {item.reasons.slice(0, 3).map((r, i) => (
            <li key={i} className="flex gap-1.5">
              <span className="text-emerald-500">✓</span>
              <span>{r}</span>
            </li>
          ))}
        </ul>
      )}

      {item.cautions.length > 0 && (
        <ul className="mt-1 space-y-0.5 text-xs text-rose-600">
          {item.cautions.map((c, i) => (
            <li key={i} className="flex gap-1.5">
              <span>⚠</span>
              <span>{c}</span>
            </li>
          ))}
        </ul>
      )}

      <div className="mt-3 flex gap-2">
        <Link
          href={`/projects/${item.project_id}`}
          className="rounded border border-slate-300 px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
        >
          案件を開く
        </Link>
        <Link
          href={`/projects/${item.project_id}?sales=1`}
          className="rounded bg-emerald-600 px-3 py-1.5 text-xs font-bold text-white hover:bg-emerald-700"
        >
          営業開始 →
        </Link>
        {/* 案件 → CRM ワンクリック登録 */}
        <CrmRegisterButton source="project" id={item.project_id} />
      </div>
    </div>
  );
}

function Toggle({
  label,
  checked,
  onChange,
}: {
  label: string;
  checked: boolean;
  onChange: (v: boolean) => void;
}) {
  return (
    <label className="flex items-center gap-1 text-xs text-slate-600">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
      />
      {label}
    </label>
  );
}

export default function RankingPanel({ reloadKey }: { reloadKey?: number }) {
  const [items, setItems] = useState<RankingItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // フィルタ・並び順
  // 営業状況フィルター。既定は「未営業のみ」（営業アクション済みは除外）。
  const [statusFilter, setStatusFilter] =
    useState<RankingStatusFilter>("not_started");
  const [contactOnly, setContactOnly] = useState(false);
  const [unsoldOnly, setUnsoldOnly] = useState(false);
  const [site, setSite] = useState<SourceSite | "">("");
  const [sort, setSort] = useState<RankingSort>("score");
  // 画面リロードなしで再取得するためのローカルトリガー（「再取得」ボタン）。
  const [localReload, setLocalReload] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchSalesRanking({
      limit: 20,
      site,
      status_filter: statusFilter,
      contact_only: contactOnly,
      unsold_only: unsoldOnly,
      sort,
    })
      .then((d) => {
        if (!active) return;
        setItems(d);
        setError(null);
      })
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [
    statusFilter,
    contactOnly,
    unsoldOnly,
    site,
    sort,
    reloadKey,
    localReload,
  ]);

  return (
    <div className="rounded-xl border border-orange-200 bg-gradient-to-br from-orange-50 to-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold text-orange-900">
          🔥 今日営業すべき案件ランキング
        </h2>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setLocalReload((k) => k + 1)}
            disabled={loading}
            className="rounded border border-orange-300 bg-white px-2.5 py-1 text-xs font-medium text-orange-800 hover:bg-orange-50 disabled:opacity-50"
            title="最新の営業状況で再取得（アクション済みを除外）"
          >
            {loading ? "更新中…" : "↻ 再取得"}
          </button>
          <label className="flex items-center gap-1 text-xs text-slate-600">
            並び順
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as RankingSort)}
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-900"
            >
              {(Object.keys(RANKING_SORT_LABELS) as RankingSort[]).map((s) => (
                <option key={s} value={s}>
                  {RANKING_SORT_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
        </div>
      </div>

      {/* フィルタ */}
      <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-1 text-xs font-medium text-slate-700">
          営業状況
          <select
            value={statusFilter}
            onChange={(e) =>
              setStatusFilter(e.target.value as RankingStatusFilter)
            }
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-900"
          >
            {(
              Object.keys(RANKING_STATUS_FILTER_LABELS) as RankingStatusFilter[]
            ).map((s) => (
              <option key={s} value={s}>
                {RANKING_STATUS_FILTER_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <Toggle label="連絡先ありのみ" checked={contactOnly} onChange={setContactOnly} />
        <Toggle label="日本未販売のみ" checked={unsoldOnly} onChange={setUnsoldOnly} />
        <label className="flex items-center gap-1 text-xs text-slate-600">
          サイト
          <select
            value={site}
            onChange={(e) => setSite(e.target.value as SourceSite | "")}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-900"
          >
            <option value="">すべて</option>
            {SALES_TARGET_SITES.map((v) => (
              <option key={v} value={v}>
                {SITE_LABELS[v]}
              </option>
            ))}
          </select>
        </label>
      </div>

      {error && <p className="mt-3 text-sm text-red-600">読み込み失敗：{error}</p>}

      <div className="mt-3 space-y-3">
        {loading && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            ランキングを算出中…
          </p>
        )}
        {!loading && items.length === 0 && !error && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-500">
            {statusFilter === "not_started"
              ? "未営業の案件はありません（営業アクション済みは除外中）。営業状況を「すべて表示」に切り替えると、返事待ち・商談中なども確認できます。"
              : "条件に合う案件がありません。フィルタを調整してください。"}
          </p>
        )}
        {!loading && items.map((item) => <RankCard key={item.project_id} item={item} />)}
      </div>
    </div>
  );
}
