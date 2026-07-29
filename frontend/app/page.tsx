"use client";

import Link from "next/link";
import { useState } from "react";

import SalesCopilotPanel from "@/components/SalesCopilotPanel";
import TodaySalesPanel from "@/components/TodaySalesPanel";
import TodayTasksPanel from "@/components/TodayTasksPanel";

// ホーム。収集・コスト等の管理系は「設定」へ、タスクの詳細リストは
// 「今日のタスク」へ移した（左メニュー 5 項目に対応）。
export default function Home() {
  const [reloadKey] = useState(0);

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-xl font-bold text-slate-900">営業ホーム</h1>
      <p className="mt-1 text-sm text-slate-500">
        今日やること・営業の進み具合をここで把握します。
      </p>

      {/* AI営業コパイロット（横断判断・最重要アクション・バケット提案） */}
      <div className="mt-6">
        <SalesCopilotPanel reloadKey={reloadKey} />
      </div>

      {/* 今日やること（営業フロー順に分類） */}
      <div className="mt-6">
        <TodayTasksPanel reloadKey={reloadKey} />
      </div>

      {/* 今日営業する案件 + 営業ダッシュボード */}
      <div className="mt-6">
        <TodaySalesPanel reloadKey={reloadKey} />
      </div>

      <div className="mt-6 flex flex-wrap gap-2">
        <Link
          href="/tasks"
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          今日のタスクを開く →
        </Link>
        <Link
          href="/projects"
          className="rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          営業案件を開く →
        </Link>
      </div>
    </main>
  );
}
