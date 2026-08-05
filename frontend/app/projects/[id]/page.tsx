"use client";

import ProductFactsPanel from "@/components/ProductFactsPanel";
import CampaignLink from "@/components/CampaignLink";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import AvailabilityPanel from "@/components/AvailabilityPanel";
import Collapsible from "@/components/Collapsible";
import CompanyResearchPanel from "@/components/CompanyResearchPanel";
import ContactDiscoveryPanel from "@/components/ContactDiscoveryPanel";
import EvaluationCard from "@/components/EvaluationCard";
import JapanSalesPanel from "@/components/JapanSalesPanel";
import OutreachPanel from "@/components/OutreachPanel";
import ProjectHistoryPanel from "@/components/ProjectHistoryPanel";
import ProjectNotesPanel from "@/components/ProjectNotesPanel";
import ReplyAssistPanel from "@/components/ReplyAssistPanel";
import SalesModeGuide from "@/components/SalesModeGuide";
import SimilarSuccessPanel from "@/components/SimilarSuccessPanel";
import SalesStatusBadge from "@/components/SalesStatusBadge";
import WadizImportPanel from "@/components/WadizImportPanel";
import WorkflowCard from "@/components/WorkflowCard";
import ArchiveReasonDialog from "@/components/ArchiveReasonDialog";
import LeadQualificationPanel from "@/components/LeadQualificationPanel";
import type { ReactNode } from "react";
import {
  archiveProject,
  evaluateProject,
  fetchEvaluations,
  fetchProject,
  formatMoney,
  fundingRate,
  htmlToText,
  isValidBusinessUrl,
  siteLabel,
  STATUS_LABELS,
  unarchiveProject,
  updateProjectStatus,
  type Evaluation,
  type Project,
  type ProjectStatus,
} from "@/lib/api";

// 案件詳細の 1 セクション。番号つきの見出しで、上から営業の思考順に並べる。
function Section({
  num,
  title,
  id,
  children,
}: {
  num: string;
  title: string;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="mt-6 scroll-mt-4">
      <h2 className="flex items-baseline gap-2 border-b border-slate-200 pb-1 text-sm font-bold text-slate-800">
        <span className="text-slate-400">{num}</span>
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  );
}

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
  // 営業状況が変わったら営業履歴セクションを取り直すための signal
  const [statusVersion, setStatusVersion] = useState(0);

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

  async function onChangeStatus(status: ProjectStatus) {
    setSaving(true);
    try {
      const updated = await updateProjectStatus(id, status);
      setProject(updated);
      setStatusVersion((v) => v + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  // 営業対象外（ソフトデリート）
  const [archiveDialogOpen, setArchiveDialogOpen] = useState(false);
  const [archiveBusy, setArchiveBusy] = useState(false);

  async function onArchive(reason?: string) {
    setArchiveBusy(true);
    try {
      const updated = await archiveProject(id, reason);
      setProject(updated);
      setArchiveDialogOpen(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  async function onRestore() {
    if (!window.confirm("この案件を営業対象に戻しますか？")) return;
    setArchiveBusy(true);
    try {
      const updated = await unarchiveProject(id);
      setProject(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  if (error) {
    return (
      <>
        <main className="mx-auto max-w-3xl px-6 py-8">
          <p className="text-red-600">読み込み失敗：{error}</p>
          <Link
            href="/projects"
            className="mt-4 inline-block text-blue-700 hover:underline"
          >
            ← 営業案件へ戻る
          </Link>
        </main>
      </>
    );
  }

  if (!project) {
    return (
      <>
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
    ["メーカー名", project.maker_name ?? "—"],
    ["サイト", siteLabel(project.source_site)],
    ["カテゴリ", project.category ?? "—"],
    ["目標金額", formatMoney(project.goal_amount, project.currency)],
    ["調達額", formatMoney(project.raised_amount, project.currency)],
    ["達成率", rate != null ? `${rate}%` : "—"],
    ["支援者数", project.backers_count?.toLocaleString() ?? "—"],
    ["掲載期間", `${project.start_date ?? "—"} 〜 ${project.end_date ?? "—"}`],
    ["連絡先候補", project.contact_info ?? "—"],
  ];

  return (
    <>
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Link href="/projects" className="text-sm text-blue-700 hover:underline">
          ← 営業案件へ戻る
        </Link>

        <div className="mt-4 flex items-start justify-between gap-4">
          <h1 className="text-2xl font-bold">{project.title}</h1>
          <div className="flex shrink-0 items-center gap-2">
            <SalesStatusBadge status={project.sales_status} />
            {!project.is_archived && (
              <button
                className="rounded border border-red-200 px-3 py-1 text-sm text-red-600 hover:bg-red-50"
                onClick={() => setArchiveDialogOpen(true)}
              >
                営業対象外にする
              </button>
            )}
          </div>
        </div>

        {project.is_archived && (
          <div className="mt-4 flex items-center justify-between gap-4 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3">
            <div className="text-sm text-amber-800">
              <span className="font-semibold">この案件は営業対象外です</span>
              （一覧・ランキング・Today Tasks・営業対象一覧から除外されています）。
              {project.archive_reason && (
                <span className="block text-xs text-amber-700">
                  理由：{project.archive_reason}
                </span>
              )}
            </div>
            <button
              className="shrink-0 rounded border border-amber-400 bg-white px-3 py-1.5 text-sm font-medium text-amber-800 hover:bg-amber-100 disabled:opacity-40"
              onClick={onRestore}
              disabled={archiveBusy}
            >
              営業対象に戻す
            </button>
          </div>
        )}

        <ArchiveReasonDialog
          open={archiveDialogOpen}
          targetLabel={project.title}
          busy={archiveBusy}
          // 営業対象判定が「対象外」の案件だけ理由をプリフィルする。
          // 証跡URL・メールアドレス・内部参照は含めない（判定値とコードのみ）。
          initialReason={
            project.lead_qualification_decision === "blocked"
              ? "営業対象判定：対象外"
              : undefined
          }
          onCancel={() => setArchiveDialogOpen(false)}
          onConfirm={onArchive}
        />

        {/* 案件詳細は「会社情報 → 営業状況 → 連絡先 → AI提案 → 営業履歴 → メモ」
            の順に並べる。まず相手を知り、次に自分の進捗を確認し、連絡手段を得て、
            AI の提案で動き、履歴とメモで引き継ぐ、という営業の思考順に対応する。 */}

        {/* ① 会社情報 */}
        <Section num="①" title="会社情報">
          {project.image_url && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={project.image_url}
              alt={project.title}
              className="w-full rounded-lg border border-slate-200 object-cover"
            />
          )}
          {summary && (
            <p className="mt-4 whitespace-pre-wrap text-sm text-slate-700">{summary}</p>
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
            {/* 海外クラファンの商品ページ（公式サイトで代用しない。未取得なら未確認表示） */}
            <CampaignLink source={project} size="md" />
            {/* メーカー公式サイトは商品ページとは別に表示する */}
            {isValidBusinessUrl(project.official_site_url ?? project.maker_url) && (
              <a
                className="text-blue-700 hover:underline"
                href={(project.official_site_url ?? project.maker_url) as string}
                target="_blank"
                rel="noreferrer"
              >
                メーカー公式 ↗
              </a>
            )}
            {isValidBusinessUrl(project.video_url) && (
              <a
                className="text-blue-700 hover:underline"
                href={project.video_url as string}
                target="_blank"
                rel="noreferrer"
              >
                動画 ↗
              </a>
            )}
          </div>

          <Collapsible
            title="📄 商品ファクトシート（確認可能な事実）"
            hint="取得元・最終確認日時つき"
          >
            <ProductFactsPanel projectId={id} />
          </Collapsible>

          <Collapsible title="🏢 企業リサーチ全文">
            <CompanyResearchPanel
              projectId={id}
              onResearched={() => setResearchVersion((v) => v + 1)}
            />
          </Collapsible>
        </Section>

        {/* ② 営業状況 */}
        <Section num="②" title="営業状況">
          <div className="flex flex-wrap items-center gap-3">
            <SalesStatusBadge status={project.sales_status} />
            <span className="text-xs text-slate-500">
              営業ワークフロー上の現在地。変更すると営業履歴に記録されます。
            </span>
          </div>

          <div className="mt-3">
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

          <Collapsible title="🧭 営業ワークフロー" hint="ステップ・チャネル・優先度">
            <WorkflowCard
              projectId={id}
              refreshKey={researchVersion + discoveryVersion}
              onSalesStatusChange={(s) => {
                setProject((p) => (p ? { ...p, sales_status: s } : p));
                setStatusVersion((v) => v + 1);
              }}
            />
          </Collapsible>

          <Collapsible title="🇯🇵 日本販売状況（詳細）">
            <JapanSalesPanel projectId={id} />
          </Collapsible>

          <Collapsible title="🛫 日本未上陸判定">
            <AvailabilityPanel projectId={id} />
          </Collapsible>
        </Section>

        {/* ③ 連絡先（Contact Intelligence）。パネル本体はここに一本化する
            （Sales Mode STEP2 はこのセクションへ誘導するだけ）。 */}
        <Section num="③" title="連絡先" id="contact-intelligence">
          <ContactDiscoveryPanel
            projectId={id}
            searchKeyword={project.maker_name?.trim() || project.title}
            onChanged={() => setDiscoveryVersion((v) => v + 1)}
          />
          {project.source_site === "wadiz" && (
            <div className="mt-3">
              <div className="mb-1 text-right">
                <Link
                  href={`/projects/${id}/wadiz-import`}
                  className="text-xs text-emerald-700 underline"
                >
                  専用画面で開く ↗
                </Link>
              </div>
              <WadizImportPanel
                projectId={id}
                defaultSourceUrl={project.source_url}
                onImported={() => setDiscoveryVersion((v) => v + 1)}
              />
            </div>
          )}
        </Section>

        {/* ③-2 営業対象判定（Lead Qualification Engine）。
            連絡先が揃ったあと、AI 提案・送信準備に進む前の関門として置く。 */}
        <Section num="③-2" title="営業対象判定">
          <LeadQualificationPanel
            projectId={id}
            projectTitle={project.title}
            onSnapshotChange={() => setStatusVersion((v) => v + 1)}
          />
        </Section>

        {/* ④ AI提案 */}
        <Section num="④" title="AI提案">
          {/* 🚀 Sales Mode：ここだけ見れば営業判断でき、営業開始でフローが進む */}
          <SalesModeGuide
            projectId={id}
            project={project}
            researchVersion={researchVersion}
            discoveryVersion={discoveryVersion}
            autoStart={autoStart}
            onDiscoveryChanged={() => setDiscoveryVersion((v) => v + 1)}
            onSalesStatusChange={(s) => {
              setProject((p) => (p ? { ...p, sales_status: s } : p));
              setStatusVersion((v) => v + 1);
            }}
          />

          <Collapsible
            title="✉️ 営業実行（メール生成・送信）"
            hint="4言語生成・下書き・営業状況"
          >
            <OutreachPanel projectId={id} />
          </Collapsible>

          <Collapsible title="✉️ 返信メールAIサポート">
            <ReplyAssistPanel projectId={id} />
          </Collapsible>

          <Collapsible title="📈 類似する日本の成功事例">
            <SimilarSuccessPanel projectId={id} />
          </Collapsible>
        </Section>

        {/* ⑤ 営業履歴 */}
        <Section num="⑤" title="営業履歴">
          <ProjectHistoryPanel projectId={id} refreshKey={statusVersion} />
        </Section>

        {/* ⑥ メーカー共通メモ（保存先は CRM のメーカー単位） */}
        <Section num="⑥" title="メーカー共通メモ">
          <ProjectNotesPanel
            projectId={id}
            makerId={project.maker_id}
            onMakerLinked={(mid) =>
              setProject((p) => (p ? { ...p, maker_id: mid } : p))
            }
          />
        </Section>
      </main>
    </>
  );
}
