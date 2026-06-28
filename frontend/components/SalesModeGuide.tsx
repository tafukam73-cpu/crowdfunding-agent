"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";

import ContactDiscoveryPanel from "@/components/ContactDiscoveryPanel";
import EmailDraftPanel from "@/components/EmailDraftPanel";
import ExecutiveSummaryPanel from "@/components/ExecutiveSummaryPanel";
import {
  createMakerFromProject,
  EXECUTIVE_CHANNEL_LABELS,
  type ExecutiveSummary,
  fetchExecutiveSummary,
  type Project,
  type SalesStatus,
  type SalesTarget,
  updateSalesStatus,
} from "@/lib/api";

const TARGET_STYLE: Record<SalesTarget, { label: string; cls: string }> = {
  yes: { label: "営業対象 YES", cls: "bg-emerald-600 text-white" },
  no: { label: "営業対象 NO", cls: "bg-rose-600 text-white" },
  "要確認": { label: "営業対象 要確認", cls: "bg-amber-500 text-white" },
};

// 4 ステップの営業フロー
const STEPS = [
  { n: 1, label: "営業判断", sub: "Executive Summary" },
  { n: 2, label: "連絡先", sub: "Contact Intelligence" },
  { n: 3, label: "メール作成", sub: "営業メール / Gmail" },
  { n: 4, label: "営業完了", sub: "営業済み / CRM" },
];

function Stars({ value }: { value: number }) {
  const n = Math.max(0, Math.min(5, value));
  return (
    <span className="text-amber-500" aria-label={`星評価 ${n} / 5`}>
      {"★".repeat(n)}
      <span className="text-slate-300">{"★".repeat(5 - n)}</span>
    </span>
  );
}

// STEP 進捗バー（現在位置を表示・クリックでジャンプ）
function StepBar({
  step,
  onJump,
}: {
  step: number;
  onJump: (n: number) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        {STEPS.map((s) => {
          const state =
            s.n < step ? "done" : s.n === step ? "current" : "todo";
          return (
            <button
              key={s.n}
              onClick={() => onJump(s.n)}
              className="flex flex-1 flex-col items-center gap-1"
            >
              <span
                className={`flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold ${
                  state === "current"
                    ? "bg-indigo-600 text-white"
                    : state === "done"
                    ? "bg-emerald-500 text-white"
                    : "bg-slate-200 text-slate-500"
                }`}
              >
                {state === "done" ? "✓" : s.n}
              </span>
              <span
                className={`text-[11px] ${
                  state === "current"
                    ? "font-bold text-indigo-700"
                    : "text-slate-500"
                }`}
              >
                STEP{s.n} {s.label}
              </span>
            </button>
          );
        })}
      </div>
      <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full bg-indigo-600 transition-all"
          style={{ width: `${(step / STEPS.length) * 100}%` }}
        />
      </div>
    </div>
  );
}

export default function SalesModeGuide({
  projectId,
  project,
  researchVersion,
  discoveryVersion,
  onDiscoveryChanged,
  onSalesStatusChange,
  autoStart = false,
}: {
  projectId: number;
  project: Project;
  researchVersion: number;
  discoveryVersion: number;
  onDiscoveryChanged: () => void;
  onSalesStatusChange: (s: SalesStatus) => void;
  autoStart?: boolean;
}) {
  const [summary, setSummary] = useState<ExecutiveSummary | null>(null);
  const [sumError, setSumError] = useState<string | null>(null);
  const [started, setStarted] = useState(autoStart);
  const [step, setStep] = useState(1);
  const guideRef = useRef<HTMLDivElement | null>(null);

  // 営業完了ステップ用
  const [savingStatus, setSavingStatus] = useState(false);
  const [statusMsg, setStatusMsg] = useState<string | null>(null);
  const [followupDate, setFollowupDate] = useState("");
  const [linking, setLinking] = useState(false);

  useEffect(() => {
    let active = true;
    fetchExecutiveSummary(projectId)
      .then((d) => active && setSummary(d))
      .catch((e) => active && setSumError(String(e)));
    return () => {
      active = false;
    };
  }, [projectId, researchVersion, discoveryVersion]);

  // ?sales=1 で来たら自動でフローを開始しスクロール
  useEffect(() => {
    if (autoStart) {
      setStarted(true);
      const t = setTimeout(
        () => guideRef.current?.scrollIntoView({ behavior: "smooth" }),
        200
      );
      return () => clearTimeout(t);
    }
  }, [autoStart]);

  function start() {
    setStarted(true);
    setStep(1);
    setTimeout(
      () => guideRef.current?.scrollIntoView({ behavior: "smooth" }),
      50
    );
  }

  async function markContacted() {
    setSavingStatus(true);
    setStatusMsg(null);
    try {
      await updateSalesStatus(projectId, "contacted");
      onSalesStatusChange("contacted");
      setStatusMsg("営業済みにしました。CRM に営業履歴を記録しました。");
    } catch (e) {
      setStatusMsg(`失敗：${String(e)}`);
    } finally {
      setSavingStatus(false);
    }
  }

  async function linkMaker() {
    setLinking(true);
    try {
      const maker = await createMakerFromProject(projectId);
      window.location.href = `/crm/makers/${maker.id}`;
    } catch (e) {
      setStatusMsg(`CRM登録に失敗：${String(e)}`);
      setLinking(false);
    }
  }

  const target = summary ? TARGET_STYLE[summary.sales_target] : null;

  return (
    <div
      ref={guideRef}
      className="rounded-xl border border-indigo-300 bg-gradient-to-br from-indigo-50 to-white p-5 shadow-sm"
    >
      {/* Sales Mode ヘッダー（ここだけ見れば営業判断できる） */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold tracking-wide text-indigo-900">
          🚀 Sales Mode
        </h2>
        {target && (
          <span className={`rounded-full px-3 py-1 text-xs font-bold ${target.cls}`}>
            {target.label}
          </span>
        )}
      </div>

      {sumError && (
        <p className="mt-2 text-sm text-red-600">要約の取得に失敗：{sumError}</p>
      )}
      {!summary && !sumError && (
        <p className="mt-2 text-sm text-slate-400">営業判断を算出中…</p>
      )}

      {summary && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-x-5 gap-y-2">
            <span className="flex items-baseline gap-1">
              <span className="text-3xl font-extrabold text-indigo-700">
                {summary.score}
              </span>
              <span className="text-xs text-slate-400">/ 100</span>
            </span>
            <Stars value={summary.stars} />
            <span className="rounded-md bg-emerald-100 px-3 py-1 text-sm font-bold text-emerald-800">
              {summary.recommended_action}
            </span>
          </div>

          <div className="mt-3 rounded-lg border border-indigo-100 bg-white/70 p-3 text-sm">
            <p className="text-xs font-semibold text-slate-500">
              推奨フロー（今日やるべきこと）
            </p>
            <p className="mt-1 text-slate-800">
              <span className="font-bold text-indigo-700">
                {EXECUTIVE_CHANNEL_LABELS[summary.recommended_channel] ??
                  summary.recommended_channel}
              </span>
              {" → 問い合わせフォーム → メール"}
            </p>
            <p className="mt-1 text-xs text-slate-500">
              日本販売：{summary.japan_sales_status} ・ 連絡先：
              {summary.contact_status}
            </p>
          </div>

          {!started && (
            <button
              onClick={start}
              className="mt-4 w-full rounded-lg bg-indigo-600 py-2.5 text-sm font-bold text-white hover:bg-indigo-700"
            >
              営業開始 →
            </button>
          )}
        </>
      )}

      {/* 営業ガイド（STEP 1〜4） */}
      {started && (
        <div className="mt-5 border-t border-indigo-100 pt-4">
          <StepBar step={step} onJump={setStep} />

          <div className="mt-4">
            {/* STEP 1: Executive Summary */}
            <div className={step === 1 ? "" : "hidden"}>
              <ExecutiveSummaryPanel
                projectId={projectId}
                refreshKey={researchVersion + discoveryVersion}
              />
            </div>

            {/* STEP 2: Contact Intelligence（フォーム/SNS/Google検索/短文DM） */}
            <div className={step === 2 ? "" : "hidden"}>
              <ContactDiscoveryPanel
                projectId={projectId}
                searchKeyword={project.maker_name?.trim() || project.title}
                onChanged={onDiscoveryChanged}
              />
            </div>

            {/* STEP 3: メール作成（営業メール / Gmail / コピー） */}
            <div className={step === 3 ? "" : "hidden"}>
              <EmailDraftPanel
                projectId={projectId}
                researchVersion={researchVersion}
                discoveryVersion={discoveryVersion}
              />
            </div>

            {/* STEP 4: 営業完了 */}
            <div className={step === 4 ? "" : "hidden"}>
              <div className="rounded-lg border border-slate-200 bg-white p-5">
                <h3 className="text-sm font-bold text-slate-800">営業完了</h3>
                <p className="mt-1 text-xs text-slate-500">
                  営業を送ったら、状況を記録してフォローアップを予定しましょう。
                </p>

                <div className="mt-4 space-y-3">
                  <button
                    onClick={markContacted}
                    disabled={savingStatus || project.sales_status === "contacted"}
                    className="rounded-lg bg-emerald-600 px-4 py-2 text-sm font-bold text-white hover:bg-emerald-700 disabled:opacity-50"
                  >
                    {project.sales_status === "contacted"
                      ? "営業済み ✓"
                      : savingStatus
                      ? "記録中…"
                      : "営業済みにする（CRMへ記録）"}
                  </button>

                  <div className="flex items-center gap-2">
                    <span className="text-sm font-semibold text-slate-600">
                      CRM：
                    </span>
                    {project.maker_id ? (
                      <Link
                        href={`/crm/makers/${project.maker_id}`}
                        className="text-sm text-blue-700 hover:underline"
                      >
                        メーカーを開く →
                      </Link>
                    ) : (
                      <button
                        onClick={linkMaker}
                        disabled={linking}
                        className="rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                      >
                        {linking ? "登録中…" : "CRMにメーカー登録"}
                      </button>
                    )}
                  </div>

                  <label className="flex flex-wrap items-center gap-2 text-sm text-slate-600">
                    フォローアップ予定日
                    <input
                      type="date"
                      value={followupDate}
                      onChange={(e) => setFollowupDate(e.target.value)}
                      className="rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                    />
                    {followupDate && (
                      <span className="text-xs text-slate-500">
                        {followupDate} にフォローアップ予定
                      </span>
                    )}
                  </label>

                  {statusMsg && (
                    <p className="text-xs text-slate-600">{statusMsg}</p>
                  )}
                </div>
              </div>
            </div>
          </div>

          {/* ステップナビゲーション */}
          <div className="mt-4 flex items-center justify-between">
            <button
              onClick={() => setStep((s) => Math.max(1, s - 1))}
              disabled={step === 1}
              className="rounded border border-slate-300 px-4 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            >
              ← 戻る
            </button>
            {step < 4 ? (
              <button
                onClick={() => setStep((s) => Math.min(4, s + 1))}
                className="rounded bg-indigo-600 px-5 py-1.5 text-sm font-bold text-white hover:bg-indigo-700"
              >
                {step === 3 ? "送信しました →" : "次へ →"}
              </button>
            ) : (
              <button
                onClick={() => setStarted(false)}
                className="rounded bg-slate-700 px-5 py-1.5 text-sm font-bold text-white hover:bg-slate-800"
              >
                フローを閉じる
              </button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
