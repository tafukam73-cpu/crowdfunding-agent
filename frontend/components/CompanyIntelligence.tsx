"use client";

import { useState } from "react";

import {
  buildMockCompanyIntelligence,
  type CompanyIntelligence as CompanyIntelligenceData,
} from "@/lib/api";

// メーカーの情報を AI が分析し、営業前に必要な情報を 1 枚で確認するカード。
// 今回は実 API 非接続。「AI分析する」で約2秒のローディング後にモックを表示する。
// デザインは SalesMakerPanel と統一（rounded-lg border border-slate-200 bg-white p-5）。

// 1 項目の表示（ラベル＋値）。値が空なら「—」を薄く表示。
function Item({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col text-xs">
      <span className="text-slate-500">{label}</span>
      <span className="mt-0.5 whitespace-pre-wrap text-sm text-slate-900">
        {value.trim() ? value : <span className="text-slate-400">—</span>}
      </span>
    </div>
  );
}

// 箇条書きの表示（営業ポイント・懸念点）。
function BulletList({
  label,
  items,
  tone,
}: {
  label: string;
  items: string[];
  tone: "good" | "warn";
}) {
  const dot = tone === "good" ? "text-green-600" : "text-amber-600";
  return (
    <div className="flex flex-col text-xs">
      <span className="text-slate-500">{label}</span>
      <ul className="mt-1 space-y-1">
        {items.length === 0 && <li className="text-slate-400">—</li>}
        {items.map((it, i) => (
          <li key={i} className="flex gap-1.5 text-sm text-slate-900">
            <span className={`mt-0.5 ${dot}`}>●</span>
            <span className="whitespace-pre-wrap">{it}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function CompanyIntelligence({
  makerId,
  seed,
}: {
  makerId?: number | string;
  // 会社名・商品名の初期値（モック文面に反映）。
  seed?: { company_name?: string; product_name?: string };
}) {
  const [data, setData] = useState<CompanyIntelligenceData | null>(null);
  const [loading, setLoading] = useState(false);

  function analyze() {
    setLoading(true);
    // 実 AI の代わりに約2秒のローディングを挟んでモックを表示。
    setTimeout(() => {
      setData(buildMockCompanyIntelligence(seed));
      setLoading(false);
    }, 2000);
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold text-slate-900">
          Company Intelligence
        </h2>
        {data && (
          <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
            モック（AI未接続）
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-slate-500">
        メーカー情報を AI が分析し、営業前に必要な情報を 1 画面で確認します。
      </p>

      {/* 操作ボタン */}
      <div className="mt-4">
        <button
          onClick={analyze}
          disabled={loading}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {loading ? "分析中…" : data ? "再分析する" : "AI分析する"}
        </button>
      </div>

      {/* ローディング */}
      {loading && (
        <div className="mt-4 flex items-center gap-2 text-sm text-slate-500">
          <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-slate-700" />
          AI が会社情報を分析しています…
        </div>
      )}

      {/* 分析結果 */}
      {!loading && data && (
        <div className="mt-4 space-y-4">
          <Item label="会社概要" value={data.company_overview} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Item label="代表者" value={data.representative} />
            <Item label="所在地" value={data.location} />
            <Item label="設立年" value={data.founded_year} />
            <Item label="社員数" value={data.employee_count} />
            <Item label="主力商品" value={data.main_products} />
            <Item label="価格帯" value={data.price_range} />
          </div>
          <Item label="ブランドイメージ" value={data.brand_image} />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Item label="競合" value={data.competitors} />
            <Item label="日本市場との相性" value={data.japan_market_fit} />
          </div>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <BulletList
              label="営業ポイント"
              items={data.sales_points}
              tone="good"
            />
            <BulletList label="懸念点" items={data.concerns} tone="warn" />
          </div>
          <Item label="推奨アプローチ" value={data.recommended_approach} />
        </div>
      )}

      {/* 未分析 */}
      {!loading && !data && (
        <p className="mt-4 text-sm text-slate-400">
          「AI分析する」を押すと、会社概要・競合・営業ポイントなどの分析結果を表示します。
        </p>
      )}
    </section>
  );
}
