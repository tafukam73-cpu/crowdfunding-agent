"use client";

import { useEffect, useState } from "react";

import { fetchUsageSummary, type UsageSummary } from "@/lib/api";

function Card({
  label,
  bucket,
}: {
  label: string;
  bucket: { cost_usd: number; input_tokens: number; output_tokens: number; calls: number };
}) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <p className="text-xs text-slate-400">{label}</p>
      <p className="mt-1 text-2xl font-bold text-slate-900">
        ${bucket.cost_usd.toFixed(4)}
      </p>
      <p className="mt-1 text-xs text-slate-400">
        {bucket.calls} 回 ・ in {bucket.input_tokens.toLocaleString()} / out{" "}
        {bucket.output_tokens.toLocaleString()} tok
      </p>
    </div>
  );
}

export default function CostPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [summary, setSummary] = useState<UsageSummary | null>(null);

  useEffect(() => {
    fetchUsageSummary()
      .then(setSummary)
      .catch(() => setSummary(null));
  }, [reloadKey]);

  return (
    <section className="mt-4">
      <h2 className="text-sm font-semibold text-slate-700">AI 利用コスト</h2>
      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
        {summary ? (
          <>
            <Card label="本日コスト" bucket={summary.today} />
            <Card label="今月コスト" bucket={summary.month} />
            <Card label="累計コスト" bucket={summary.total} />
          </>
        ) : (
          <p className="text-sm text-slate-400">コスト情報を取得できません</p>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-400">
        ※ Claude 実行分のみ集計（モック評価はコスト 0 のため記録なし）。UTC 基準。
      </p>
    </section>
  );
}
