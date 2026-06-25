"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AvailabilityPanel from "@/components/AvailabilityPanel";
import EmailDraftPanel from "@/components/EmailDraftPanel";
import EvaluationCard from "@/components/EvaluationCard";
import Header from "@/components/Header";
import SimilarSuccessPanel from "@/components/SimilarSuccessPanel";
import StatusBadge from "@/components/StatusBadge";
import {
  createMakerFromProject,
  evaluateProject,
  fetchEvaluations,
  fetchProject,
  formatMoney,
  fundingRate,
  SITE_LABELS,
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

  useEffect(() => {
    fetchProject(id)
      .then(setProject)
      .catch((e) => setError(String(e)));
    fetchEvaluations(id)
      .then((list) => setEvaluation(list[0] ?? null))
      .catch(() => setEvaluation(null));
  }, [id]);

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

  const rows: [string, string][] = [
    ["サイト", SITE_LABELS[project.source_site]],
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

        {project.image_url && (
          // eslint-disable-next-line @next/next/no-img-element
          <img
            src={project.image_url}
            alt={project.title}
            className="mt-4 w-full rounded-lg border border-slate-200 object-cover"
          />
        )}

        {project.description && (
          <p className="mt-4 whitespace-pre-wrap text-slate-700">
            {project.description}
          </p>
        )}

        <dl className="mt-6 grid grid-cols-[8rem_1fr] gap-y-3 rounded-lg border border-slate-200 bg-white p-5 text-sm">
          {rows.map(([label, value]) => (
            <div key={label} className="contents">
              <dt className="text-slate-500">{label}</dt>
              <dd className="text-slate-900">{value}</dd>
            </div>
          ))}
        </dl>

        {/* 外部リンク */}
        <div className="mt-4 flex flex-wrap gap-4 text-sm">
          {project.source_url && (
            <a
              className="text-blue-700 hover:underline"
              href={project.source_url}
              target="_blank"
              rel="noreferrer"
            >
              案件ページ ↗
            </a>
          )}
          {project.maker_url && (
            <a
              className="text-blue-700 hover:underline"
              href={project.maker_url}
              target="_blank"
              rel="noreferrer"
            >
              メーカー公式 ↗
            </a>
          )}
          {project.video_url && (
            <a
              className="text-blue-700 hover:underline"
              href={project.video_url}
              target="_blank"
              rel="noreferrer"
            >
              動画 ↗
            </a>
          )}
        </div>

        {/* CRM 連携 */}
        <div className="mt-6 flex items-center gap-3 rounded-lg border border-slate-200 bg-white p-4 text-sm">
          <span className="font-semibold text-slate-700">営業管理（CRM）</span>
          {project.maker_id ? (
            <Link
              href={`/crm/makers/${project.maker_id}`}
              className="text-blue-700 hover:underline"
            >
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

        {/* ステータス変更 */}
        <div className="mt-8 rounded-lg border border-slate-200 bg-white p-5">
          <h2 className="text-sm font-semibold text-slate-700">営業ステータス変更</h2>
          <div className="mt-3 flex flex-wrap gap-2">
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

        {/* AI 評価 */}
        <div className="mt-8">
          <div className="flex items-center justify-between">
            <h2 className="text-sm font-semibold text-slate-700">AI 評価</h2>
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
              <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
                まだ評価されていません。「AI評価する」を押すと評価が生成されます。
              </p>
            )}
          </div>
        </div>

        {/* 日本未上陸判定 */}
        <AvailabilityPanel projectId={id} />

        {/* 類似する日本の成功事例 */}
        <SimilarSuccessPanel projectId={id} />

        {/* 営業メール下書き */}
        <EmailDraftPanel projectId={id} />
      </main>
    </>
  );
}
