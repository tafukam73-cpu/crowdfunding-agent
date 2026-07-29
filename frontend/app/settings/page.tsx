"use client";

import Link from "next/link";
import { useState } from "react";

import CostPanel from "@/components/CostPanel";
import ScheduleStatusPanel from "@/components/ScheduleStatusPanel";
import ScrapePanel from "@/components/ScrapePanel";
import ScrapeStatsPanel from "@/components/ScrapeStatsPanel";
import { evaluateRun, fetchEvaluateEstimate } from "@/lib/api";

// 設定。左メニューから外した管理系（収集・コスト・CRM・成功事例）の入口を
// ここに集約し、日々の営業動線（ホーム / 営業案件 / 今日のタスク / AI秘書）と分ける。
//
// 「営業管理ツール」は独立セクションにする。左メニューの「営業案件」＝案件一覧
// （/projects）と紛らわしくならないよう、連絡先探索由来のものは「営業候補管理」
// と呼び分ける（ルート・機能はそのまま）。
const LINK_GROUPS: {
  title: string;
  desc: string;
  links: { href: string; label: string; desc: string }[];
}[] = [
  {
    title: "営業管理ツール",
    desc: "案件一覧（営業案件）とは別に、連絡先・企業単位で管理する画面です。",
    links: [
      {
        href: "/sales-opportunities",
        label: "営業候補管理（Contact Intelligence案件）",
        desc: "連絡先探索から作成した営業候補のステータス・次アクション・期限管理",
      },
      {
        href: "/crm",
        label: "CRM（メーカー・連絡先）",
        desc: "メーカー単位の連絡先・活動履歴・メーカー共通メモ",
      },
    ],
  },
  {
    title: "メール・参考データ",
    desc: "営業メールの生成設定と、比較に使う日本の成功事例です。",
    links: [
      {
        href: "/email-settings",
        label: "メール設定",
        desc: "差出人・署名・会社概要（営業メール生成に反映）",
      },
      {
        href: "/japanese-success",
        label: "日本の成功事例",
        desc: "Makuake / GreenFunding の成功案件（比較用）",
      },
    ],
  },
];

export default function SettingsPage() {
  const [reloadKey, setReloadKey] = useState(0);
  const [costKey, setCostKey] = useState(0);
  const [evaluating, setEvaluating] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      // バックグラウンド評価。少し待ってからコストを再読込
      setTimeout(() => setCostKey((k) => k + 1), 1500);
    } catch (e) {
      setError(String(e));
    } finally {
      setTimeout(() => setEvaluating(false), 1500);
    }
  }

  return (
    <main className="mx-auto max-w-5xl px-6 py-8">
      <h1 className="text-xl font-bold text-slate-900">設定</h1>
      <p className="mt-1 text-sm text-slate-500">
        メール・データ収集・コストなどの管理機能をまとめています。
      </p>

      {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

      {/* 各設定・管理画面への入口（用途ごとのセクションに分ける） */}
      {LINK_GROUPS.map((g) => (
        <section key={g.title} className="mt-6">
          <h2 className="text-sm font-bold text-slate-800">{g.title}</h2>
          <p className="mt-0.5 text-xs text-slate-500">{g.desc}</p>
          <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
            {g.links.map((l) => (
              <Link
                key={l.href}
                href={l.href}
                className="rounded-lg border border-slate-200 bg-white p-4 transition hover:border-slate-400 hover:bg-slate-50"
              >
                <p className="text-sm font-semibold text-slate-800">{l.label} →</p>
                <p className="mt-1 text-xs text-slate-500">{l.desc}</p>
              </Link>
            ))}
          </div>
        </section>
      ))}

      {/* AI 評価の一括実行（コストの目安を確認してから実行する） */}
      <div className="mt-6 flex flex-wrap items-center justify-between gap-3 rounded-lg border border-slate-200 bg-white px-4 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-800">AI 評価の一括実行</p>
          <p className="text-xs text-slate-500">
            未評価の案件をまとめて AI 評価します（実行前に推定コストを表示）。
          </p>
        </div>
        <button
          onClick={onEvaluateAll}
          disabled={evaluating}
          className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
        >
          {evaluating ? "評価中…" : "未評価をAI評価"}
        </button>
      </div>

      {/* 収集コントロール */}
      <div className="mt-6">
        <ScrapePanel onCompleted={() => setReloadKey((k) => k + 1)} />
      </div>

      {/* 自動収集（日次スケジューラ）の状況 */}
      <div className="mt-6">
        <ScheduleStatusPanel onCompleted={() => setReloadKey((k) => k + 1)} />
      </div>

      {/* 取得モニタリング（成功率・構造変化・403 等） */}
      <div className="mt-6" key={reloadKey}>
        <ScrapeStatsPanel />
      </div>

      {/* AI 利用コスト */}
      <CostPanel reloadKey={costKey} />
    </main>
  );
}
