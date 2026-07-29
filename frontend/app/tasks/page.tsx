"use client";

import { useState } from "react";

import ExecutionTasksPanel from "@/components/ExecutionTasksPanel";
import RankingPanel from "@/components/RankingPanel";
import TodayPriorityPanel from "@/components/TodayPriorityPanel";
import TodayTasksPanel from "@/components/TodayTasksPanel";

// 今日のタスク。ホームは「今日やることの要約」に絞り、実行用の詳細リストは
// このページへ集約する（既存パネルをそのまま再配置）。
export default function TasksPage() {
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">今日のタスク</h1>
          <p className="mt-1 text-sm text-slate-500">
            今日営業する案件・フォロー期日・返信対応を、営業フロー順に確認して実行します。
          </p>
        </div>
        <button
          onClick={() => setReloadKey((k) => k + 1)}
          className="shrink-0 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          再読み込み
        </button>
      </div>

      {/* 今日やること（営業フロー順に分類） */}
      <div className="mt-6">
        <TodayTasksPanel reloadKey={reloadKey} />
      </div>

      {/* 送信後ワークフロー：今日フォロー・期限超過・返信対応 */}
      <div className="mt-6">
        <ExecutionTasksPanel reloadKey={reloadKey} />
      </div>

      {/* 営業実行パイプライン：今日営業する案件（優先度順） */}
      <div className="mt-6">
        <TodayPriorityPanel reloadKey={reloadKey} />
      </div>

      {/* AI 営業優先ランキング（Executive Summary 統合） */}
      <div className="mt-6">
        <RankingPanel reloadKey={reloadKey} />
      </div>
    </main>
  );
}
