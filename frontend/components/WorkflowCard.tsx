"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchWorkflow,
  SALES_STATUS_COLORS,
  SALES_STATUS_LABELS,
  updateSalesStatus,
  type SalesStatus,
  type Workflow,
} from "@/lib/api";

// 「営業状況を変更」ボタンに出す遷移先（表示順）。
const STATUS_ACTIONS: { status: SalesStatus; label: string }[] = [
  { status: "contacted", label: "営業済みにする" },
  { status: "awaiting_reply", label: "返信待ち" },
  { status: "replied", label: "返信あり" },
  { status: "negotiating", label: "商談中" },
  { status: "won", label: "契約成立" },
  { status: "rejected", label: "見送り" },
];

function Stars({ n }: { n: number }) {
  const full = Math.max(0, Math.min(5, n));
  return (
    <span className="text-amber-500" aria-label={`優先度 ${full} / 5`}>
      {"★".repeat(full)}
      <span className="text-slate-300">{"☆".repeat(5 - full)}</span>
    </span>
  );
}

// 営業ワークフローカード。案件詳細の最上部に表示し、「何からやればいいか」を
// 順番に案内する（リサーチ→連絡先→メール→DM→営業開始→営業済み）。
export default function WorkflowCard({
  projectId,
  refreshKey = 0,
  onSalesStatusChange,
}: {
  projectId: number;
  // 他パネル（リサーチ/連絡先/メール生成）の更新を反映させるためのシグナル
  refreshKey?: number;
  onSalesStatusChange?: (status: SalesStatus) => void;
}) {
  const [wf, setWf] = useState<Workflow | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<SalesStatus | null>(null);

  const load = useCallback(() => {
    fetchWorkflow(projectId)
      .then((w) => {
        setWf(w);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, [projectId]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  async function onChange(status: SalesStatus) {
    setBusy(status);
    try {
      await updateSalesStatus(projectId, status);
      onSalesStatusChange?.(status);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="rounded-lg border border-indigo-200 bg-indigo-50/60 p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-bold text-indigo-900">営業ワークフロー</h2>
        {wf && (
          <div className="flex items-center gap-2 text-xs">
            <Stars n={wf.stars} />
            <span className="text-slate-500">優先度 {wf.priority_score}/100</span>
            <span
              className={`rounded px-2 py-0.5 font-medium ${SALES_STATUS_COLORS[wf.sales_status]}`}
            >
              {SALES_STATUS_LABELS[wf.sales_status]}
            </span>
          </div>
        )}
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {!wf && !error && (
        <p className="mt-3 text-sm text-slate-400">読み込み中…</p>
      )}

      {wf && (
        <>
          {/* ステップ ①〜④（完了判定） */}
          <ol className="mt-4 flex flex-wrap items-stretch gap-2">
            {wf.steps.map((s, i) => (
              <li key={s.key} className="flex items-center gap-2">
                <div
                  className={`min-w-[8rem] rounded-md border px-3 py-2 ${
                    s.done
                      ? "border-emerald-300 bg-emerald-50"
                      : "border-slate-300 bg-white"
                  }`}
                >
                  <p className="text-xs text-slate-500">
                    {["①", "②", "③", "④"][i] ?? ""} {s.label}
                  </p>
                  <p
                    className={`text-sm font-semibold ${
                      s.done ? "text-emerald-700" : "text-slate-400"
                    }`}
                  >
                    {s.done ? "✔ 完了" : "未完了"}
                  </p>
                </div>
                {i < wf.steps.length - 1 && (
                  <span className="text-slate-300">→</span>
                )}
              </li>
            ))}
          </ol>

          {/* ⑤ 営業開始：URL のあるチャネルだけ「開く」ボタン */}
          <div className="mt-4">
            <p className="text-xs font-semibold text-slate-500">⑤ 営業開始</p>
            {wf.channels.length === 0 ? (
              <p className="mt-1 text-xs text-slate-400">
                開けるチャネルがまだありません。先に連絡先探索を実行してください。
              </p>
            ) : (
              <div className="mt-1.5 flex flex-wrap gap-2">
                {wf.channels.map((c) => (
                  <a
                    key={c.key}
                    href={c.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className={
                      c.recommended
                        ? "inline-flex items-center gap-1 rounded-md border border-emerald-400 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 hover:bg-emerald-100"
                        : "inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
                    }
                  >
                    {c.recommended ? `★ おすすめ：${c.label}` : `${c.label}を開く`}
                    <span aria-hidden>↗</span>
                  </a>
                ))}
              </div>
            )}
          </div>

          {/* ⑥ 営業状況の変更 */}
          <div className="mt-4">
            <p className="text-xs font-semibold text-slate-500">
              ⑥ 営業状況を更新（CRMに営業履歴を自動記録）
            </p>
            <div className="mt-1.5 flex flex-wrap gap-2">
              {STATUS_ACTIONS.map((a) => {
                const current = wf.sales_status === a.status;
                return (
                  <button
                    key={a.status}
                    disabled={current || busy !== null}
                    onClick={() => onChange(a.status)}
                    className={`rounded border px-3 py-1 text-xs transition ${
                      current
                        ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-300 text-slate-700 hover:bg-slate-50"
                    } disabled:opacity-50`}
                  >
                    {busy === a.status ? "更新中…" : a.label}
                  </button>
                );
              })}
            </div>
          </div>
        </>
      )}
    </div>
  );
}
