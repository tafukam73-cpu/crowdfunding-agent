"use client";

import { useEffect, useState } from "react";

import {
  EXECUTIVE_CHANNEL_LABELS,
  type ExecutiveSummary,
  fetchExecutiveSummary,
  type SalesTarget,
} from "@/lib/api";

// 営業対象（YES / NO / 要確認）の色分け
const TARGET_STYLE: Record<SalesTarget, { label: string; cls: string }> = {
  yes: { label: "営業対象：YES", cls: "bg-emerald-600 text-white" },
  no: { label: "営業対象：NO", cls: "bg-rose-600 text-white" },
  "要確認": { label: "営業対象：要確認", cls: "bg-amber-500 text-white" },
};

// 推奨アクションの強調色（「今すぐ営業」を最も目立たせる）
const ACTION_STYLE: Record<string, string> = {
  今すぐ営業: "bg-emerald-100 text-emerald-800 border-emerald-300",
  連絡先探索が必要: "bg-sky-100 text-sky-800 border-sky-300",
  日本販売状況を確認: "bg-amber-100 text-amber-800 border-amber-300",
  営業対象外の可能性: "bg-rose-100 text-rose-800 border-rose-300",
  後回し: "bg-slate-100 text-slate-700 border-slate-300",
};

function Stars({ value }: { value: number }) {
  const n = Math.max(0, Math.min(5, value));
  return (
    <span className="text-2xl leading-none text-amber-500" aria-label={`星評価 ${n} / 5`}>
      {"★".repeat(n)}
      <span className="text-slate-300">{"★".repeat(5 - n)}</span>
    </span>
  );
}

// スコアに応じた色（高=緑、中=琥珀、低=赤）
function scoreColor(score: number): string {
  if (score >= 65) return "text-emerald-600";
  if (score >= 40) return "text-amber-600";
  return "text-rose-600";
}

function InfoRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <span className="min-w-[7.5rem] text-xs font-semibold text-slate-500">
        {label}
      </span>
      <span className="text-sm text-slate-800">{value}</span>
    </div>
  );
}

export default function ExecutiveSummaryPanel({
  projectId,
  refreshKey,
}: {
  projectId: number;
  refreshKey?: number;
}) {
  const [data, setData] = useState<ExecutiveSummary | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchExecutiveSummary(projectId)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [projectId, refreshKey]);

  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        Executive Summary の取得に失敗しました：{error}
      </div>
    );
  }

  if (!data) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-5 text-sm text-slate-400">
        AI Executive Summary を算出中…
      </div>
    );
  }

  const target = TARGET_STYLE[data.sales_target];
  const actionCls =
    ACTION_STYLE[data.recommended_action] ??
    "bg-slate-100 text-slate-700 border-slate-300";

  return (
    <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-white p-5 shadow-sm">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-sm font-bold tracking-wide text-indigo-900">
          ⚡ AI Executive Summary
        </h2>
        <span className={`rounded-full px-3 py-1 text-xs font-bold ${target.cls}`}>
          {target.label}
        </span>
      </div>

      {/* スコア・星・推奨アクション（大きく表示） */}
      <div className="mt-3 flex flex-wrap items-center gap-x-6 gap-y-3">
        <div className="flex items-baseline gap-1">
          <span className={`text-4xl font-extrabold ${scoreColor(data.score)}`}>
            {data.score}
          </span>
          <span className="text-sm text-slate-400">/ 100</span>
        </div>
        <Stars value={data.stars} />
        <span
          className={`rounded-md border px-3 py-1.5 text-sm font-bold ${actionCls}`}
        >
          {data.recommended_action}
        </span>
        <span className="rounded border border-slate-300 bg-white px-2 py-1 text-xs text-slate-600">
          推奨チャネル：
          {EXECUTIVE_CHANNEL_LABELS[data.recommended_channel] ??
            data.recommended_channel}
        </span>
      </div>

      {/* 主要指標 */}
      <div className="mt-4 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        <InfoRow label="商品ジャンル" value={data.product_category} />
        <InfoRow label="日本市場との相性" value={data.japan_market_fit} />
        <InfoRow label="日本販売状況" value={data.japan_sales_status} />
        <InfoRow label="日本代理店" value={data.japan_distributor_status} />
        <InfoRow label="連絡先取得状況" value={data.contact_status} />
      </div>

      {/* 理由・注意点 */}
      <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div>
          <p className="text-xs font-semibold text-emerald-700">主な理由</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm text-slate-700">
            {data.reasons.map((r, i) => (
              <li key={i}>{r}</li>
            ))}
          </ul>
        </div>
        <div>
          <p className="text-xs font-semibold text-rose-700">注意点</p>
          <ul className="mt-1 list-disc space-y-0.5 pl-4 text-sm text-slate-700">
            {data.cautions.map((c, i) => (
              <li key={i}>{c}</li>
            ))}
          </ul>
        </div>
      </div>

      <p className="mt-3 text-[11px] text-slate-400">
        ※ 既存の AI評価・日本販売状況・連絡先探索・企業リサーチ・類似事例を統合した自動要約です。
        下の各カードで詳細を確認・実行できます。
      </p>
    </div>
  );
}
