"use client";

import CampaignLink from "@/components/CampaignLink";
import Link from "next/link";
import { useEffect, useState } from "react";

import ArchiveReasonDialog from "@/components/ArchiveReasonDialog";
import CrmRegisterButton from "@/components/CrmRegisterButton";
import SourceBadge from "@/components/SourceBadge";
import SalesStatusBadge from "@/components/SalesStatusBadge";
import QualificationBadge from "@/components/QualificationBadge";
import {
  AVAILABILITY_COLORS,
  AVAILABILITY_LABELS,
  archiveProject,
  bulkArchiveProjects,
  fetchProjects,
  fundingRate,
  formatDateTime,
  formatMoney,
  QUALIFICATION_LABELS,
  REC_LABELS,
  SALES_STATUS_LABELS,
  SALES_STATUS_ORDER,
  SALES_TARGET_SITES,
  SITE_LABELS,
  unarchiveProject,
  type ListParams,
  type Project,
  type ProjectList,
  type QualificationDecision,
  type Recommendation,
  type SalesStatus,
  type SourceSite,
} from "@/lib/api";

const PAGE_SIZE = 20;

/** 募集状況（事実）。終了日が無ければ「未取得」。予測は行わない。 */
function campaignState(p: Project): { label: string; tone: string } {
  if (!p.end_date) return { label: "未取得", tone: "text-slate-400" };
  const end = new Date(p.end_date);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (end < today) return { label: "終了", tone: "text-slate-400" };
  const days = Math.ceil((end.getTime() - today.getTime()) / 86400000);
  return {
    label: `残り${days}日`,
    tone: days <= 7 ? "font-medium text-rose-600" : "text-emerald-700",
  };
}

/** 適用中の絞り込みを 1 行で見えるようにするチップ。 */
function FilterChip({ label, onClear }: { label: string; onClear: () => void }) {
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-slate-100 px-2.5 py-0.5 text-xs text-slate-700">
      {label}
      <button
        onClick={onClear}
        aria-label={`${label} を解除`}
        className="text-slate-400 hover:text-slate-700"
      >
        ×
      </button>
    </span>
  );
}

// 営業案件一覧。基本フィルター（検索・営業状況）は常時表示し、使用頻度の低い
// 条件（サイト・推奨度・並び替え）は「詳細条件」に折りたたむ。
export default function ProjectsPage() {
  const [data, setData] = useState<ProjectList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [site, setSite] = useState<SourceSite | "">("");
  const [salesStatus, setSalesStatus] = useState<SalesStatus | "">("");
  const [q, setQ] = useState("");
  const [recommendation, setRecommendation] = useState<Recommendation | "">("");
  // 営業対象判定（**pre_research スナップショット**）での絞り込み。
  const [qualification, setQualification] = useState<QualificationDecision | "">("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);

  // 営業対象外（ソフトデリート）関連
  const [showArchived, setShowArchived] = useState(false);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<Project | "bulk" | null>(null);

  // ホームの KPI カードなどから ?sales_status=... で絞り込んだ状態で開ける。
  useEffect(() => {
    if (typeof window === "undefined") return;
    const sp = new URLSearchParams(window.location.search);
    const s = sp.get("sales_status");
    if (s && (SALES_STATUS_ORDER as string[]).includes(s)) {
      setSalesStatus(s as SalesStatus);
    }
    if (sp.get("sort") === "updated_at") {
      setSort("updated_at");
    }
  }, []);

  useEffect(() => {
    const params: ListParams = {
      site,
      sales_status: salesStatus,
      q,
      recommendation,
      qualification,
      archived: showArchived,
      sort,
      order,
      page,
      page_size: PAGE_SIZE,
    };
    setLoading(true);
    fetchProjects(params)
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [site, salesStatus, q, recommendation, qualification, showArchived, sort, order, page, reloadKey]);

  // 表示条件が変わったら選択をクリア（別ページ/別ビューの選択を持ち越さない）。
  useEffect(() => {
    setSelected(new Set());
  }, [site, salesStatus, q, recommendation, qualification, showArchived, page, reloadKey]);

  function toggleSelected(id: number) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function toggleSelectAll() {
    const ids = data?.items.map((p) => p.id) ?? [];
    setSelected((prev) =>
      ids.every((id) => prev.has(id)) ? new Set() : new Set(ids)
    );
  }

  function clearFilters() {
    setPage(1);
    setSite("");
    setSalesStatus("");
    setQ("");
    setRecommendation("");
    setQualification("");
  }

  // 単体：営業対象外にする（理由つき）。ダイアログの確定から呼ぶ。
  async function doArchiveOne(project: Project, reason?: string) {
    setArchiveBusy(true);
    try {
      await archiveProject(project.id, reason);
      setArchiveTarget(null);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  // 一括：選択案件を営業対象外にする（理由つき）。
  async function doArchiveBulk(reason?: string) {
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setArchiveBusy(true);
    try {
      await bulkArchiveProjects(ids, reason);
      setArchiveTarget(null);
      setSelected(new Set());
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    } finally {
      setArchiveBusy(false);
    }
  }

  // 復元（除外済み一覧から通常一覧へ戻す）。
  async function doRestore(project: Project) {
    if (!window.confirm(`「${project.title}」を営業対象に戻しますか？`)) return;
    try {
      await unarchiveProject(project.id);
      setReloadKey((k) => k + 1);
    } catch (e) {
      setError(String(e));
    }
  }

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const hasFilter = Boolean(site || salesStatus || q || recommendation || qualification);
  const colCount = showArchived ? 9 : 10;

  return (
    <>
      <main className="mx-auto max-w-6xl px-6 py-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              {showArchived ? "除外済み案件（営業対象外）" : "営業案件"}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {showArchived
                ? "営業対象外にした案件です。「復元」で通常の営業対象に戻せます（調査結果や営業履歴は削除されていません）。"
                : "Kickstarter / Indiegogo / Wadiz / zeczec の営業対象案件。案件名をクリックすると詳細を開きます。"}
            </p>
          </div>
          <button
            className="shrink-0 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50"
            onClick={() => {
              setPage(1);
              setShowArchived((v) => !v);
            }}
          >
            {showArchived ? "← 営業対象一覧へ戻る" : "除外済み案件を表示"}
          </button>
        </div>

        {/* 一括操作バー：選択があるときだけ表示（件数を明示） */}
        {!showArchived && selected.size > 0 && (
          <div className="mt-4 flex items-center justify-between rounded-lg border border-amber-200 bg-amber-50 px-4 py-2">
            <span className="text-sm text-amber-800">{selected.size} 件を選択中</span>
            <div className="flex items-center gap-2">
              <button
                className="rounded border border-slate-300 bg-white px-3 py-1 text-sm text-slate-600 hover:bg-slate-50"
                onClick={() => setSelected(new Set())}
              >
                選択解除
              </button>
              <button
                className="rounded bg-red-600 px-3 py-1 text-sm font-medium text-white hover:bg-red-700"
                onClick={() => setArchiveTarget("bulk")}
              >
                選択した {selected.size} 件を営業対象外にする
              </button>
            </div>
          </div>
        )}

        {/* フィルター：基本条件は常時表示、それ以外は「詳細条件」に折りたたむ */}
        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="min-w-[14rem] flex-1 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-900 placeholder:text-slate-400"
              placeholder="案件名で検索"
              value={q}
              onChange={(e) => {
                setPage(1);
                setQ(e.target.value);
              }}
            />
            <select
              className="rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
              value={salesStatus}
              aria-label="営業状況"
              onChange={(e) => {
                setPage(1);
                setSalesStatus(e.target.value as SalesStatus | "");
              }}
            >
              <option value="">営業状況：すべて</option>
              {SALES_STATUS_ORDER.map((v) => (
                <option key={v} value={v}>
                  {SALES_STATUS_LABELS[v]}
                </option>
              ))}
            </select>
            {hasFilter && (
              <button
                onClick={clearFilters}
                className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                条件をクリア
              </button>
            )}
          </div>

          <details className="group mt-2">
            <summary className="inline-flex cursor-pointer list-none items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800">
              <span className="transition group-open:rotate-90">▶</span>
              詳細条件（サイト・推奨度・並び替え）
            </summary>
            <div className="mt-2 flex flex-wrap items-end gap-3 border-t border-slate-100 pt-3">
              <label className="flex flex-col text-xs text-slate-500">
                サイト
                <select
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                  value={site}
                  onChange={(e) => {
                    setPage(1);
                    setSite(e.target.value as SourceSite | "");
                  }}
                >
                  <option value="">すべて</option>
                  {SALES_TARGET_SITES.map((v) => (
                    <option key={v} value={v}>
                      {SITE_LABELS[v]}
                    </option>
                  ))}
                </select>
              </label>

              <label className="flex flex-col text-xs text-slate-500">
                推奨度
                <select
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                  value={recommendation}
                  onChange={(e) => {
                    setPage(1);
                    setRecommendation(e.target.value as Recommendation | "");
                  }}
                >
                  <option value="">すべて</option>
                  <option value="high">高</option>
                  <option value="mid">中</option>
                  <option value="low">低</option>
                </select>
              </label>

              <label className="flex flex-col text-xs text-slate-500">
                営業対象判定
                <select
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                  value={qualification}
                  onChange={(e) => {
                    setPage(1);
                    setQualification(e.target.value as QualificationDecision | "");
                  }}
                  title="調査前（pre_research）の判定で絞り込みます"
                >
                  <option value="">すべて</option>
                  <option value="blocked">{QUALIFICATION_LABELS.blocked}</option>
                  <option value="review">{QUALIFICATION_LABELS.review}</option>
                  <option value="clear">{QUALIFICATION_LABELS.clear}</option>
                </select>
              </label>

              <label className="flex flex-col text-xs text-slate-500">
                並び替え
                <select
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                  value={sort}
                  onChange={(e) => setSort(e.target.value)}
                >
                  <option value="created_at">登録日</option>
                  <option value="updated_at">更新日</option>
                  <option value="raised_amount">調達額</option>
                  <option value="backers_count">支援者数</option>
                  <option value="end_date">終了日</option>
                  <option value="title">案件名</option>
                </select>
              </label>

              <label className="flex flex-col text-xs text-slate-500">
                順序
                <select
                  className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
                  value={order}
                  onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
                >
                  <option value="desc">降順</option>
                  <option value="asc">昇順</option>
                </select>
              </label>
            </div>
          </details>

          {/* 適用中の条件（何で絞っているかを常に見えるようにする） */}
          {hasFilter && (
            <div className="mt-2 flex flex-wrap items-center gap-1.5 border-t border-slate-100 pt-2">
              {q && <FilterChip label={`検索：${q}`} onClear={() => setQ("")} />}
              {salesStatus && (
                <FilterChip
                  label={`営業状況：${SALES_STATUS_LABELS[salesStatus]}`}
                  onClear={() => setSalesStatus("")}
                />
              )}
              {site && (
                <FilterChip
                  label={`サイト：${SITE_LABELS[site]}`}
                  onClear={() => setSite("")}
                />
              )}
              {recommendation && (
                <FilterChip
                  label={`推奨度：${REC_LABELS[recommendation]}`}
                  onClear={() => setRecommendation("")}
                />
              )}
              {qualification && (
                <FilterChip
                  label={`営業対象判定：${QUALIFICATION_LABELS[qualification]}`}
                  onClear={() => setQualification("")}
                />
              )}
            </div>
          )}
        </div>

        {error && (
          <p className="mt-6 text-red-600">
            読み込み失敗：{error}（バックエンド http://localhost:8000 を確認）
          </p>
        )}

        {/* 一覧テーブル：見出しは固定、数値は右寄せで桁を揃える */}
        <div className="mt-4 overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="sticky top-0 z-10 bg-slate-50 text-left text-xs text-slate-500 shadow-[0_1px_0_0_rgb(226,232,240)]">
              <tr>
                {!showArchived && (
                  <th className="w-8 px-3 py-2.5">
                    <input
                      type="checkbox"
                      aria-label="すべて選択"
                      checked={
                        (data?.items.length ?? 0) > 0 &&
                        data!.items.every((p) => selected.has(p.id))
                      }
                      onChange={toggleSelectAll}
                    />
                  </th>
                )}
                <th className="px-4 py-2.5">案件名</th>
                <th className="whitespace-nowrap px-4 py-2.5">営業状況</th>
                <th className="whitespace-nowrap px-4 py-2.5" title="調査前（pre_research）の判定">
                  営業対象判定
                </th>
                <th className="whitespace-nowrap px-4 py-2.5">募集</th>
                <th className="whitespace-nowrap px-4 py-2.5">日本販売</th>
                <th className="whitespace-nowrap px-4 py-2.5 text-right">調達額</th>
                <th className="whitespace-nowrap px-4 py-2.5 text-right">達成率</th>
                <th className="whitespace-nowrap px-4 py-2.5 text-right">支援者</th>
                <th className="whitespace-nowrap px-4 py-2.5">更新</th>
                <th className="px-4 py-2.5">操作</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => {
                const rate = fundingRate(p);
                const state = campaignState(p);
                return (
                  <tr
                    key={p.id}
                    className="border-t border-slate-100 align-top transition hover:bg-sky-50/60"
                  >
                    {!showArchived && (
                      <td className="px-3 py-3">
                        <input
                          type="checkbox"
                          aria-label={`${p.title} を選択`}
                          checked={selected.has(p.id)}
                          onChange={() => toggleSelected(p.id)}
                        />
                      </td>
                    )}
                    <td className="max-w-[24rem] px-4 py-3">
                      <Link
                        href={`/projects/${p.id}`}
                        className="line-clamp-2 font-medium text-blue-700 hover:underline"
                      >
                        {p.title}
                      </Link>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <SourceBadge site={p.source_site} />
                        <CampaignLink source={p} />
                        <span className="text-xs text-slate-400">
                          {p.category ?? "—"}
                        </span>
                      </div>
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <SalesStatusBadge status={p.sales_status} />
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      <QualificationBadge
                        decision={p.lead_qualification_decision}
                        title={
                          p.lead_qualification_at
                            ? `調査前の判定 / ${formatDateTime(p.lead_qualification_at)}`
                            : "調査前の判定（未判定）"
                        }
                      />
                    </td>
                    <td className={`whitespace-nowrap px-4 py-3 text-xs ${state.tone}`}>
                      {state.label}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      {p.latest_availability ? (
                        <span
                          className={`rounded px-2 py-0.5 text-xs font-medium ${AVAILABILITY_COLORS[p.latest_availability]}`}
                        >
                          {AVAILABILITY_LABELS[p.latest_availability]}
                        </span>
                      ) : (
                        <span className="text-xs text-slate-300">—</span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                      {formatMoney(p.raised_amount, p.currency)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                      {rate != null ? (
                        <span
                          className={
                            rate >= 100 ? "font-medium text-emerald-700" : undefined
                          }
                        >
                          {rate}%
                        </span>
                      ) : (
                        "—"
                      )}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-right tabular-nums">
                      {p.backers_count?.toLocaleString() ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3 text-xs text-slate-500">
                      {formatDateTime(p.updated_at)}
                    </td>
                    <td className="whitespace-nowrap px-4 py-3">
                      {showArchived ? (
                        <div className="flex flex-col gap-1">
                          <button
                            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
                            onClick={() => doRestore(p)}
                          >
                            復元
                          </button>
                          {p.archive_reason && (
                            <span className="text-[11px] text-slate-400">
                              理由：{p.archive_reason}
                            </span>
                          )}
                        </div>
                      ) : (
                        <div className="flex flex-col items-start gap-1.5">
                          <CrmRegisterButton
                            source="project"
                            id={p.id}
                            initialMakerId={p.maker_id}
                          />
                          <button
                            className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                            onClick={() => setArchiveTarget(p)}
                          >
                            営業対象外
                          </button>
                        </div>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={colCount} className="px-4 py-10 text-center text-slate-400">
                    {showArchived
                      ? "除外済みの案件はありません"
                      : hasFilter
                        ? "条件に一致する案件がありません。「条件をクリア」で全件表示できます。"
                        : "案件がありません"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ページング */}
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>
            {data ? `全 ${data.total} 件` : ""}
            {loading && "（読み込み中…）"}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              前へ
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </button>
          </div>
        </div>
      </main>

      {/* 営業対象外の確認ダイアログ（単体 / 一括で共用） */}
      <ArchiveReasonDialog
        open={archiveTarget !== null}
        targetLabel={
          archiveTarget && archiveTarget !== "bulk" ? archiveTarget.title : undefined
        }
        count={archiveTarget === "bulk" ? selected.size : undefined}
        busy={archiveBusy}
        onCancel={() => setArchiveTarget(null)}
        onConfirm={(reason) => {
          if (archiveTarget === "bulk") doArchiveBulk(reason);
          else if (archiveTarget) doArchiveOne(archiveTarget, reason);
        }}
      />
    </>
  );
}
