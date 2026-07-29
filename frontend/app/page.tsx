"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import CostPanel from "@/components/CostPanel";
import ExecutionTasksPanel from "@/components/ExecutionTasksPanel";
import Header from "@/components/Header";
import RankingPanel from "@/components/RankingPanel";
import SalesCopilotPanel from "@/components/SalesCopilotPanel";
import TodayTasksPanel from "@/components/TodayTasksPanel";
import ScheduleStatusPanel from "@/components/ScheduleStatusPanel";
import ScrapePanel from "@/components/ScrapePanel";
import ScrapeStatsPanel from "@/components/ScrapeStatsPanel";
import TodayPriorityPanel from "@/components/TodayPriorityPanel";
import TodaySalesPanel from "@/components/TodaySalesPanel";
import { fetchEvaluateEstimate, evaluateRun } from "@/lib/api";

export default function Home() {
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [costKey, setCostKey] = useState(0);
  const [evaluating, setEvaluating] = useState(false);

  // 未評価（AI未評価）件数。件数の内訳を明示して収集履歴との矛盾をなくす。
  const [unevaluated, setUnevaluated] = useState<number | null>(null);

  useEffect(() => {
    let active = true;
    fetchEvaluateEstimate()
      .then((est) => active && setUnevaluated(est.count))
      .catch(() => active && setUnevaluated(null));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  async function onEvaluateAll() {
    try {
      // 実行前に推定コストを表示して確認
      const est = await fetchEvaluateEstimate();
      if (est.count === 0) {
        window.alert("未評価の案件はありません。");
        return;
      }
      const msg =
        est.mode === "claude"
          ? `未評価 ${est.count} 件を ${est.model} で評価します。\n` +
            `推定: 入力 ${est.est_input_tokens.toLocaleString()} tok / ` +
            `出力 ${est.est_output_tokens.toLocaleString()} tok / ` +
            `約 $${est.est_cost_usd.toFixed(4)}\n実行しますか？`
          : `未評価 ${est.count} 件をモック評価します（コスト $0）。実行しますか？`;
      if (!window.confirm(msg)) return;
    } catch (e) {
      setError(String(e));
      return;
    }

    setEvaluating(true);
    try {
      await evaluateRun();
      // バックグラウンド評価。少し待ってから再読込
      setTimeout(() => {
        setReloadKey((k) => k + 1);
        setCostKey((k) => k + 1);
      }, 1500);
    } catch (e) {
      setError(String(e));
    } finally {
      setTimeout(() => setEvaluating(false), 1500);
    }
  }

  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        {error && <p className="mb-4 text-sm text-red-600">{error}</p>}

        {/* AI営業コパイロット（横断判断・最重要アクション・バケット提案） */}
        <div className="mb-6">
          <SalesCopilotPanel reloadKey={reloadKey} />
        </div>

        {/* 今日やること（営業フロー順に分類） */}
        <div className="mb-6">
          <TodayTasksPanel reloadKey={reloadKey} />
        </div>

        {/* AI 営業優先ランキング（Executive Summary 統合） */}
        <div className="mb-6">
          <RankingPanel reloadKey={reloadKey} />
        </div>

        {/* 営業実行パイプライン：今日営業する案件（優先度順） */}
        <div className="mb-6">
          <TodayPriorityPanel reloadKey={reloadKey} />
        </div>

        {/* 送信後ワークフロー：今日フォロー・期限超過・返信対応 */}
        <div className="mb-6">
          <ExecutionTasksPanel reloadKey={reloadKey} />
        </div>

        {/* 今日営業する案件 + 営業ダッシュボード */}
        <div className="mb-6">
          <TodaySalesPanel reloadKey={reloadKey} />
        </div>

        {/* 案件一覧は「営業案件」ページへ分離した（フィルター整理・視認性改善） */}
        <div className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-slate-800">営業案件一覧</p>
            <p className="text-xs text-slate-500">
              案件の絞り込み・営業対象外の整理は「営業案件」ページで行います。
              {unevaluated != null && `（AI未評価 ${unevaluated} 件）`}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={onEvaluateAll}
              disabled={evaluating}
              className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              {evaluating ? "評価中…" : "未評価をAI評価"}
            </button>
            <Link
              href="/projects"
              className="rounded bg-slate-900 px-3 py-1.5 text-sm font-medium text-white hover:bg-slate-700"
            >
              営業案件を開く →
            </Link>
          </div>
        </div>

        {/* 収集コントロール */}
        <ScrapePanel onCompleted={() => setReloadKey((k) => k + 1)} />

        {/* 自動収集（日次スケジューラ）の状況 */}
        <div className="mt-6">
          <ScheduleStatusPanel onCompleted={() => setReloadKey((k) => k + 1)} />
        </div>

        {/* 取得モニタリング（成功率・構造変化・403 等） */}
        <div className="mt-6">
          <ScrapeStatsPanel />
        </div>

        {/* AI 利用コスト */}
        <CostPanel reloadKey={costKey} />
      </main>
    </>
  );
}
