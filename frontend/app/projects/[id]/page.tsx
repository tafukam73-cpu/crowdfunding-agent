"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AvailabilityPanel from "@/components/AvailabilityPanel";
import Collapsible from "@/components/Collapsible";
import CompanyResearchPanel from "@/components/CompanyResearchPanel";
import EvaluationCard from "@/components/EvaluationCard";
import Header from "@/components/Header";
import JapanSalesPanel from "@/components/JapanSalesPanel";
import ReplyAssistPanel from "@/components/ReplyAssistPanel";
import SalesModeGuide from "@/components/SalesModeGuide";
import SimilarSuccessPanel from "@/components/SimilarSuccessPanel";
import StatusBadge from "@/components/StatusBadge";
import WorkflowCard from "@/components/WorkflowCard";
import {
  createMakerFromProject,
  evaluateProject,
  fetchEvaluations,
  fetchProject,
  formatMoney,
  fundingRate,
  htmlToText,
  siteLabel,
  STATUS_LABELS,
  updateProjectStatus,
  type Evaluation,
  type Project,
  type ProjectStatus,
} from "@/lib/api";

export default function ProjectDetail() {
  const params = useParams();
  const id = Number(params.id);

  const [project, setProject] = useState<Project | null>(null);
  const [evaluation, setEvaluation] = useState<Evaluation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  // 企業リサーチ実行後にメール下書きパネルの「反映済み」表示を更新するための signal
  const [researchVersion, setResearchVersion] = useState(0);
  // 連絡先探索の更新を宛先候補へ反映するための signal
  const [discoveryVersion, setDiscoveryVersion] = useState(0);
  // ランキング等から ?sales=1 で来たら営業フローを自動開始する
  const [autoStart, setAutoStart] = useState(false);

  useEffect(() => {
    fetchProject(id)
      .then(setProject)
      .catch((e) => setError(String(e)));
    fetchEvaluations(id)
      .then((list) => setEvaluation(list[0] ?? null))
      .catch(() => setEvaluation(null));
  }, [id]);

  useEffect(() => {
    if (typeof window !== "undefined") {
      setAutoStart(new URLSearchParams(window.location.search).get("sales") === "1");
    }
  }, []);

  async function onEvaluate() {
    setEvaluating(true);
    try {
      const ev = await evaluateProject(id);
      setEvaluation(ev);
      // 最新スコアの反映のため案件も再取得
      setProject(await fetchProject(id));
    } catch (e) {
      setError(String(e));
    } finally {
      setEvaluating(false);
    }
  }

  const [linking, setLinking] = useState(false);

  async function onLinkMaker() {
    setLinking(true);
    try {
      const maker = await createMakerFromProject(id);
      window.location.href = `/crm/makers/${maker.id}`;
    } catch (e) {
      setError(String(e));
      setLinking(false);
    }
  }

  async function onChangeStatus(status: ProjectStatus) {
    setSaving(true);
    try {
      const updated = await updateProjectStatus(id, status);
      setProject(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  if (error) {
    return (
      <>
        <Header />
        <main className="mx-auto max-w-3xl px-6 py-8">
          <p className="text-red-600">読み込み失敗：{error}</p>
          <Link href="/" className="mt-4 inline-block text-blue-700 hover:underline">
            ← 一覧へ戻る
          </Link>
        </main>
      </>
    );
  }

  if (!project) {
    return (
      <>
        <Header />
        <main className="mx-auto max-w-3xl px-6 py-8 text-slate-400">読み込み中…</main>
      </>
    );
  }

  const rate = fundingRate(project);

  // 概要表示：description_clean（HTML除去済み）を優先。空ならクライアント側で
  // 生 description を sanitize してから表示する（HTMLタグを画面に出さない）。
  const summary =
    project.description_clean?.trim() || htmlToText(project.description);

  const rows: [string, string][] = [
    ["サイト", siteLabel(project.source_site)],
    ["カテゴリ", project.category ?? "—"],
    ["目標金額", formatMoney(project.goal_amount, project.currency)],
    ["調達額", formatMoney(project.raised_amount, project.currency)],
    ["達成率", rate != null ? `${rate}%` : "—"],
    ["支援者数", project.backers_count?.toLocaleString() ?? "—"],
    ["掲載期間", `${project.start_date ?? "—"} 〜 ${project.end_date ?? "—"}`],
    ["メーカー名", project.maker_name ?? "—"],
    ["連絡先候補", project.contact_info ?? "—"],
  ];

  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Link href="/" className="text-sm text-blue-700 hover:underline">
          ← 一覧へ戻る
        </Link>

        <div className="mt-4 flex items-start justify-between gap-4">
          <h1 className="text-2xl font-bold">{project.title}</h1>
          <StatusBadge status={project.status} />
        </div>

        {project.is_sales_target_candidate === false && (
          <p className="mt-3 inline-flex items-center gap-1 rounded-md border border-amber-300 bg-amber-50 px-3 py-1.5 text-sm font-medium text-amber-800">
            ⚠ 営業対象外の可能性あり（寄付・観光・文化活動・イベント・団体支援など、物販ではない案件の可能性）
          </p>
        )}

        {/* 🚀 Sales Mode：ここだけ見れば営業判断でき、営業開始でフローが進む */}
        <div className="mt-4">
          <SalesModeGuide
            projectId={id}
            project={project}
            researchVersion={researchVersion}
            discoveryVersion={discoveryVersion}
            autoStart={autoStart}
            onDiscoveryChanged={() => setDiscoveryVersion((v) => v + 1)}
            onSalesStatusChange={(s) =>
              setProject((p) => (p ? { ...p, sales_status: s } : p))
            }
          />
        </div>

        {/* ここから下は営業で今すぐ必要ない詳細（折りたたみ） */}
        <div className="mt-6">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            詳細情報（必要なときに開く）
          </h2>

          <Collapsible title="🧭 営業ワークフロー" hint="ステップ・チャネル・優先度">
            <WorkflowCard
              projectId={id}
              refreshKey={researchVersion + discoveryVersion}
              onSalesStatusChange={(s) =>
                setProject((p) => (p ? { ...p, sales_status: s } : p))
              }
            />
          </Collapsible>

          <Collapsible title="📊 AI評価" hint={evaluation ? `${evaluation.total_score}点` : "未評価"}>
            <div className="flex items-center justify-end">
              <button
                onClick={onEvaluate}
                disabled={evaluating}
                className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                {evaluating ? "評価中…" : evaluation ? "再評価" : "AI評価する"}
              </button>
            </div>
            <div className="mt-3">
              {evaluation ? (
                <EvaluationCard ev={evaluation} />
              ) : (
                <p className="text-sm text-slate-400">
                  まだ評価されていません。「AI評価する」を押すと評価が生成されます。
                </p>
              )}
            </div>
          </Collapsible>

          <Collapsible title="🇯🇵 日本販売状況（詳細）">
            <JapanSalesPanel projectId={id} />
          </Collapsible>

          <Collapsible title="🏢 企業リサーチ全文">
            <CompanyResearchPanel
              projectId={id}
              onResearched={() => setResearchVersion((v) => v + 1)}
            />
          </Collapsible>

          <Collapsible title="🛫 日本未上陸判定">
            <AvailabilityPanel projectId={id} />
          </Collapsible>

          <Collapsible title="📈 類似する日本の成功事例">
            <SimilarSuccessPanel projectId={id} />
          </Collapsible>

          <Collapsible title="✉️ 返信メールAIサポート">
            <ReplyAssistPanel projectId={id} />
          </Collapsible>

          <Collapsible title="📋 案件情報・スクレイピング情報">
            {project.image_url && (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={project.image_url}
                alt={project.title}
                className="w-full rounded-lg border border-slate-200 object-cover"
              />
            )}
            {summary && (
              <p className="mt-4 whitespace-pre-wrap text-sm text-slate-700">
                {summary}
              </p>
            )}
            <dl className="mt-4 grid grid-cols-[8rem_1fr] gap-y-3 text-sm">
              {rows.map(([label, value]) => (
                <div key={label} className="contents">
                  <dt className="text-slate-500">{label}</dt>
                  <dd className="text-slate-900">{value}</dd>
                </div>
              ))}
            </dl>
            <div className="mt-4 flex flex-wrap gap-4 text-sm">
              {project.source_url && (
                <a className="text-blue-700 hover:underline" href={project.source_url} target="_blank" rel="noreferrer">
                  案件ページ ↗
                </a>
              )}
              {project.maker_url && (
                <a className="text-blue-700 hover:underline" href={project.maker_url} target="_blank" rel="noreferrer">
                  メーカー公式 ↗
                </a>
              )}
              {project.video_url && (
                <a className="text-blue-700 hover:underline" href={project.video_url} target="_blank" rel="noreferrer">
                  動画 ↗
                </a>
              )}
            </div>
          </Collapsible>

          <Collapsible title="🤝 営業管理（CRM）・ステータス変更">
            <div className="flex items-center gap-3 text-sm">
              <span className="font-semibold text-slate-700">CRM</span>
              {project.maker_id ? (
                <Link href={`/crm/makers/${project.maker_id}`} className="text-blue-700 hover:underline">
                  メーカーを開く →
                </Link>
              ) : (
                <button
                  onClick={onLinkMaker}
                  disabled={linking}
                  className="rounded border border-slate-300 px-3 py-1 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {linking ? "登録中…" : "CRMにメーカー登録"}
                </button>
              )}
            </div>
            <div className="mt-4">
              <p className="text-sm font-semibold text-slate-700">営業ステータス変更</p>
              <div className="mt-2 flex flex-wrap gap-2">
                {(Object.keys(STATUS_LABELS) as ProjectStatus[]).map((s) => (
                  <button
                    key={s}
                    disabled={saving || project.status === s}
                    onClick={() => onChangeStatus(s)}
                    className={`rounded border px-3 py-1 text-sm transition ${
                      project.status === s
                        ? "border-slate-900 bg-slate-900 text-white"
                        : "border-slate-300 text-slate-700 hover:bg-slate-50"
                    } disabled:opacity-50`}
                  >
                    {STATUS_LABELS[s]}
                  </button>
                ))}
              </div>
            </div>
          </Collapsible>
        </div>
      </main>
    </>
  );
}
