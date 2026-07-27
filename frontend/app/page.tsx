"use client";

import CampaignLink from "@/components/CampaignLink";
import Link from "next/link";
import { useEffect, useState } from "react";

import ArchiveReasonDialog from "@/components/ArchiveReasonDialog";
import CostPanel from "@/components/CostPanel";
import CrmRegisterButton from "@/components/CrmRegisterButton";
import ExecutionTasksPanel from "@/components/ExecutionTasksPanel";
import Header from "@/components/Header";
import RankingPanel from "@/components/RankingPanel";
import SalesCopilotPanel from "@/components/SalesCopilotPanel";
import TodayTasksPanel from "@/components/TodayTasksPanel";
import ScheduleStatusPanel from "@/components/ScheduleStatusPanel";
import ScrapePanel from "@/components/ScrapePanel";
import ScrapeStatsPanel from "@/components/ScrapeStatsPanel";
import SourceBadge from "@/components/SourceBadge";
import TodayPriorityPanel from "@/components/TodayPriorityPanel";
import TodaySalesPanel from "@/components/TodaySalesPanel";
import StatusBadge from "@/components/StatusBadge";
import {
  AVAILABILITY_COLORS,
  AVAILABILITY_LABELS,
  archiveProject,
  bulkArchiveProjects,
  evaluateRun,
  fetchEvaluateEstimate,
  fetchProjects,
  fundingRate,
  formatDateTime,
  formatMoney,
  SALES_TARGET_SITES,
  SITE_LABELS,
  STATUS_LABELS,
  unarchiveProject,
  type ListParams,
  type Project,
  type ProjectList,
  type ProjectStatus,
  type Recommendation,
  type SourceSite,
} from "@/lib/api";

const PAGE_SIZE = 10;

/** 募集状況（事実）。終了日が無ければ「未取得」。予測は行わない。 */
function campaignState(p: Project): string {
  if (!p.end_date) return "未取得";
  const end = new Date(p.end_date);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  if (end < today) return "終了";
  const days = Math.ceil((end.getTime() - today.getTime()) / 86400000);
  return `募集中（残り${days}日）`;
}

export default function Home() {
  const [data, setData] = useState<ProjectList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const [site, setSite] = useState<SourceSite | "">("");
  const [status, setStatus] = useState<ProjectStatus | "">("");
  const [q, setQ] = useState("");
  const [recommendation, setRecommendation] = useState<Recommendation | "">("");
  const [sort, setSort] = useState("created_at");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);
  const [reloadKey, setReloadKey] = useState(0);
  const [costKey, setCostKey] = useState(0);
  const [evaluating, setEvaluating] = useState(false);
  // 未評価（AI未評価）件数。件数の内訳を明示して収集履歴との矛盾をなくす。
  const [unevaluated, setUnevaluated] = useState<number | null>(null);

  // 営業対象外（ソフトデリート）関連
  const [showArchived, setShowArchived] = useState(false); // 除外済み案件を表示
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [archiveBusy, setArchiveBusy] = useState(false);
  // ダイアログ対象：単体案件 / "bulk"（複数選択）/ null（閉じている）
  const [archiveTarget, setArchiveTarget] = useState<Project | "bulk" | null>(null);

  useEffect(() => {
    const params: ListParams = {
      site,
      status,
      q,
      recommendation,
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
  }, [site, status, q, recommendation, showArchived, sort, order, page, reloadKey]);

  // 表示条件が変わったら選択をクリア（別ページ/別ビューの選択を持ち越さない）。
  useEffect(() => {
    setSelected(new Set());
  }, [site, status, q, recommendation, showArchived, page, reloadKey]);

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

  // 単体：営業対象外にする（理由つき）。ダイアログの確定から呼ぶ。
  async function doArchiveOne(project: Project, reason?: string) {
    setArchiveBusy(true);
    try {
      await archiveProject(project.id, reason);
      setArchiveTarget(null);
      setReloadKey((k) => k + 1);
      setCostKey((k) => k + 1);
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
      setCostKey((k) => k + 1);
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

  // 未評価件数を取得（件数内訳の表示・「未評価をAI評価」の目安に使う）。
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
      // バックグラウンド評価。少し待ってから一覧・コストを再読込
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

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
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

        <div className="mt-8 flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold">
              {showArchived ? "除外済み案件（営業対象外）" : "海外営業対象案件"}
            </h1>
            <p className="mt-1 text-sm text-slate-500">
              {showArchived ? (
                <>
                  営業対象外にした案件です。「復元」で通常の営業対象に戻せます（調査結果や
                  営業履歴は削除されていません）。
                </>
              ) : (
                <>
                  Kickstarter / Indiegogo / Wadiz の案件のみ。日本の成功事例（Makuake /
                  GreenFunding）は
                  <Link href="/japanese-success" className="text-blue-700 hover:underline">
                    日本の成功事例
                  </Link>
                  で確認できます。
                </>
              )}
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
            <span className="text-sm text-amber-800">
              {selected.size} 件を選択中
            </span>
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

        {/* フィルタ */}
        <div className="mt-4 flex flex-wrap items-end gap-3">
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
            ステータス
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={status}
              onChange={(e) => {
                setPage(1);
                setStatus(e.target.value as ProjectStatus | "");
              }}
            >
              <option value="">すべて</option>
              {Object.entries(STATUS_LABELS).map(([v, label]) => (
                <option key={v} value={v}>
                  {label}
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
            並び替え
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="created_at">登録日</option>
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

          <label className="flex flex-1 flex-col text-xs text-slate-500">
            案件名検索
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="キーワード"
              value={q}
              onChange={(e) => {
                setPage(1);
                setQ(e.target.value);
              }}
            />
          </label>
        </div>

        {error && (
          <p className="mt-6 text-red-600">
            読み込み失敗：{error}（バックエンド http://localhost:8000 を確認）
          </p>
        )}

        {/* 一覧テーブル */}
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                {!showArchived && (
                  <th className="px-3 py-2">
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
                <th className="px-4 py-2">案件名</th>
                <th className="px-4 py-2">サイト</th>
                <th className="px-4 py-2">募集状況</th>
                <th className="px-4 py-2">日本販売確認</th>
                <th className="px-4 py-2">調達額</th>
                <th className="px-4 py-2">達成率</th>
                <th className="px-4 py-2">支援者</th>
                <th className="px-4 py-2">ステータス</th>
                <th className="px-4 py-2">取得日時</th>
                <th className="px-4 py-2">CRM</th>
                <th className="px-4 py-2">操作</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((p) => {
                const rate = fundingRate(p);
                return (
                  <tr key={p.id} className="border-t border-slate-100 hover:bg-slate-50">
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
                    <td className="px-4 py-3">
                      <Link
                        href={`/projects/${p.id}`}
                        className="font-medium text-blue-700 hover:underline"
                      >
                        {p.title}
                      </Link>
                      <div className="mt-1 flex flex-wrap items-center gap-2">
                        <CampaignLink source={p} />
                        <span className="text-xs text-slate-400">{p.category ?? "—"}</span>
                      </div>
                    </td>
                    <td className="px-4 py-3">
                      <SourceBadge site={p.source_site} />
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-600">
                      {campaignState(p)}
                    </td>
                    <td className="px-4 py-3">
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
                    <td className="px-4 py-3">
                      {formatMoney(p.raised_amount, p.currency)}
                    </td>
                    <td className="px-4 py-3">{rate != null ? `${rate}%` : "—"}</td>
                    <td className="px-4 py-3">
                      {p.backers_count?.toLocaleString() ?? "—"}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge status={p.status} />
                    </td>
                    <td className="px-4 py-3 text-xs text-slate-500">
                      {formatDateTime(p.updated_at)}
                    </td>
                    <td className="px-4 py-3">
                      <CrmRegisterButton
                        source="project"
                        id={p.id}
                        initialMakerId={p.maker_id}
                      />
                    </td>
                    <td className="px-4 py-3">
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
                        <button
                          className="rounded border border-red-200 px-2 py-1 text-xs text-red-600 hover:bg-red-50"
                          onClick={() => setArchiveTarget(p)}
                        >
                          営業対象外
                        </button>
                      )}
                    </td>
                  </tr>
                );
              })}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={12} className="px-4 py-8 text-center text-slate-400">
                    {showArchived
                      ? "除外済みの案件はありません"
                      : "該当する案件がありません"}
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
