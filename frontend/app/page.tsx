"use client";

import { useState } from "react";

import HomeAiSecretary from "@/components/HomeAiSecretary";
import HomeKpiCards from "@/components/HomeKpiCards";
import HomePriorityTop5 from "@/components/HomePriorityTop5";
import HomeRecentProjects from "@/components/HomeRecentProjects";
import HomeTodayCard from "@/components/HomeTodayCard";

// 営業AIホーム（Home Dashboard v2）。
// 朝これを開けば「今日やること」「パイプラインの現在地」「着手すべき案件」が
// 分かる構成。実行用の詳細リストは「今日のタスク」「営業案件」へ送る。
export default function Home() {
  const [reloadKey, setReloadKey] = useState(0);

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold text-slate-900">営業ホーム</h1>
          <p className="mt-1 text-sm text-slate-500">
            今日やること・営業の進み具合・着手すべき案件をここで把握します。
          </p>
        </div>
        <button
          onClick={() => setReloadKey((k) => k + 1)}
          className="shrink-0 rounded border border-slate-300 bg-white px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
        >
          再読み込み
        </button>
      </div>

      {/* ① 今日やること */}
      <div className="mt-6">
        <HomeTodayCard reloadKey={reloadKey} />
      </div>

      {/* ② KPI（返信待ち・交渉中・契約目前・販売中） */}
      <div className="mt-6">
        <HomeKpiCards reloadKey={reloadKey} />
      </div>

      {/* ③ 優先案件 TOP5 ／ ④ 最近更新された案件 ／ ⑤ AI秘書（土台） */}
      <div className="mt-6 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="space-y-4 lg:col-span-2">
          <HomePriorityTop5 reloadKey={reloadKey} />
          <HomeRecentProjects reloadKey={reloadKey} />
        </div>
        <HomeAiSecretary reloadKey={reloadKey} />
      </div>
    </main>
  );
}
