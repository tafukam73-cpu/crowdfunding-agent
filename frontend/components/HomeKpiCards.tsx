"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  fetchSalesDashboard,
  type SalesDashboard,
  type SalesStatus,
} from "@/lib/api";

// パイプラインの現在地を 4 つの数字で示す。カードを押すと営業案件一覧が
// その営業状況で絞り込まれた状態で開く。
const CARDS: {
  key: keyof SalesDashboard;
  status: SalesStatus;
  label: string;
  hint: string;
  cls: string;
}[] = [
  {
    key: "awaiting_reply_count",
    status: "awaiting_reply",
    label: "返信待ち",
    hint: "初回営業済み・返信待ち",
    cls: "border-yellow-200 bg-yellow-50 text-yellow-800",
  },
  {
    key: "negotiating_count",
    status: "negotiating",
    label: "交渉中",
    hint: "商談・条件のすり合わせ",
    cls: "border-purple-200 bg-purple-50 text-purple-800",
  },
  {
    key: "contract_agreed_count",
    status: "contract_agreed",
    label: "契約目前",
    hint: "契約合意（輸入準備の手前）",
    cls: "border-green-200 bg-green-50 text-green-800",
  },
  {
    key: "selling_count",
    status: "selling",
    label: "販売中",
    hint: "日本販売を開始済み",
    cls: "border-emerald-200 bg-emerald-50 text-emerald-800",
  },
];

export default function HomeKpiCards({ reloadKey = 0 }: { reloadKey?: number }) {
  const [dash, setDash] = useState<SalesDashboard | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchSalesDashboard()
      .then((d) => {
        if (!active) return;
        setDash(d);
        setError(null);
      })
      .catch(() => active && setError("集計を取得できませんでした"));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <section>
      <div className="flex items-baseline justify-between">
        <h2 className="text-sm font-bold text-slate-800">営業パイプライン</h2>
        {error && <span className="text-xs text-red-600">{error}</span>}
      </div>
      <div className="mt-2 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {CARDS.map((c) => (
          <Link
            key={c.key}
            href={`/projects?sales_status=${c.status}`}
            className={`rounded-lg border p-4 transition hover:brightness-95 ${c.cls}`}
          >
            <p className="text-xs font-medium">{c.label}</p>
            <p className="mt-1 text-3xl font-bold tabular-nums">
              {dash ? (dash[c.key] as number) : "—"}
            </p>
            <p className="mt-1 text-[11px] opacity-75">{c.hint}</p>
          </Link>
        ))}
      </div>
    </section>
  );
}
