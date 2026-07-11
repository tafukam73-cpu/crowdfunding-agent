"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import Header from "@/components/Header";
import {
  fetchSalesCopilotV2,
  fetchProjectCopilotV2,
  runSalesAssessment,
  formatDateTime,
  siteLabel,
  siteColor,
  type SalesCopilotV2Card,
  type SalesCopilotV2Dashboard,
  type ScoreGrade,
  type V2Decision,
} from "@/lib/api";
import {
  DECISION_COLORS,
  DECISION_LABELS,
  FILTER_LABELS,
  GRADE_COLORS,
  JAPAN_RESULT_LABELS,
  JAPAN_STATUS_LABELS,
  MISSING_DATA_LABELS,
  SORT_LABELS,
  STATE_COLORS,
  STATE_LABELS,
  matchesFilter,
  sortCards,
  type V2FilterKey,
  type V2SortKey,
} from "@/lib/salesCopilotV2";

function ScoreBadge({
  score,
  grade,
  label,
}: {
  score: number | null;
  grade: ScoreGrade | null;
  label: string;
}) {
  return (
    <div className="flex flex-col items-center">
      <span className="text-[10px] text-slate-400">{label}</span>
      <span className="flex items-center gap-1">
        <span className="text-sm font-semibold text-slate-800">
          {score ?? "-"}
        </span>
        {grade && (
          <span
            className={`rounded px-1 text-[10px] font-bold ${GRADE_COLORS[grade]}`}
          >
            {grade}
          </span>
        )}
      </span>
    </div>
  );
}

function DecisionBadge({ decision }: { decision: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        DECISION_COLORS[decision] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {DECISION_LABELS[decision as V2Decision] ?? decision}
    </span>
  );
}

function CardDetail({
  card,
  onRerun,
  rerunning,
}: {
  card: SalesCopilotV2Card;
  onRerun: () => void;
  rerunning: boolean;
}) {
  const a = card.assessment;
  const j = card.japan_sales_check;
  const blocks = [
    { key: "japan_market_fit", label: "日本市場適性", b: a.japan_market_fit },
    { key: "exclusivity", label: "独占販売可能性", b: a.exclusivity },
    { key: "makuake_fit", label: "Makuake適性", b: a.makuake_fit },
  ];
  return (
    <div className="border-t border-slate-100 bg-slate-50 px-4 py-3 text-sm">
      {/* 3スコアの内訳・理由 */}
      <div className="grid gap-3 md:grid-cols-3">
        {blocks.map(({ key, label, b }) => (
          <div key={key} className="rounded border border-slate-200 bg-white p-2">
            <div className="flex items-center justify-between">
              <span className="font-medium text-slate-700">{label}</span>
              <span className="flex items-center gap-1">
                <span className="font-semibold">{b.score ?? "-"}</span>
                {b.grade && (
                  <span className={`rounded px-1 text-[10px] font-bold ${GRADE_COLORS[b.grade]}`}>
                    {b.grade}
                  </span>
                )}
              </span>
            </div>
            <ul className="mt-1 list-disc pl-4 text-xs text-slate-500">
              {b.reasons.map((r, i) => (
                <li key={i}>{r}</li>
              ))}
            </ul>
          </div>
        ))}
      </div>

      {/* confidence / missing_data / v1v2 比較 */}
      <div className="mt-3 grid gap-3 md:grid-cols-2">
        <div className="rounded border border-slate-200 bg-white p-2">
          <div className="text-xs font-medium text-slate-600">
            confidence: <span className="font-semibold">{a.confidence ?? "-"}</span>
            {a.engine && <span className="ml-2 text-slate-400">engine: {a.engine}</span>}
          </div>
          {a.missing_data && a.missing_data.length > 0 && (
            <div className="mt-1 text-xs text-slate-500">
              不足データ:{" "}
              {a.missing_data.map((m) => MISSING_DATA_LABELS[m] ?? m).join(" / ")}
            </div>
          )}
          <div className="mt-1 text-xs text-slate-500">
            評価日時: {formatDateTime(a.evaluated_at)}
          </div>
        </div>
        <div className="rounded border border-slate-200 bg-white p-2">
          <div className="text-xs text-slate-600">
            v1判断: <span className="font-medium">{card.v1_decision_label ?? card.v1_decision}</span>
            {" → "}
            v2判断: <DecisionBadge decision={card.v2_decision} />
          </div>
          {card.decision_changed && card.decision_change_reason && (
            <div className="mt-1 text-xs text-indigo-600">
              変更理由: {card.decision_change_reason}
            </div>
          )}
        </div>
      </div>

      {/* 日本販売チェックの根拠 */}
      <div className="mt-3 rounded border border-slate-200 bg-white p-2">
        <div className="text-xs font-medium text-slate-700">
          日本販売状況: {JAPAN_STATUS_LABELS[j.status]}
          {j.result && (
            <span className="ml-2 text-slate-600">
              {JAPAN_RESULT_LABELS[j.result] ?? j.result}（確度 {j.confidence}）
            </span>
          )}
        </div>
        {j.error_reason && (
          <div className="mt-1 text-xs text-rose-600">失敗理由: {j.error_reason}</div>
        )}
        {j.evidence.length > 0 && (
          <ul className="mt-1 list-disc pl-4 text-xs text-slate-500">
            {j.evidence.map((e, i) => (
              <li key={i}>{e}</li>
            ))}
          </ul>
        )}
        {j.source_urls.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-2 text-xs">
            {j.source_urls.slice(0, 5).map((u, i) => (
              <a key={i} href={u} target="_blank" rel="noreferrer" className="text-sky-600 underline">
                根拠URL{i + 1}
              </a>
            ))}
          </div>
        )}
      </div>

      {/* 推奨アクション + 導線 */}
      <div className="mt-3 flex flex-wrap items-center gap-3">
        <span className="text-xs text-slate-600">
          推奨: <span className="font-medium">{card.next_action ?? "-"}</span>
        </span>
        <a
          href={`/projects/${card.project_id}`}
          className="rounded bg-slate-800 px-2 py-1 text-xs text-white hover:bg-slate-700"
        >
          案件詳細（メール生成・Contact Intelligence）
        </a>
        <button
          onClick={onRerun}
          disabled={rerunning}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        >
          {rerunning ? "再評価中…" : "Assessmentを再実行"}
        </button>
      </div>
    </div>
  );
}

function V2Row({
  card,
  onCardUpdate,
}: {
  card: SalesCopilotV2Card;
  onCardUpdate: (c: SalesCopilotV2Card) => void;
}) {
  const [open, setOpen] = useState(false);
  const [rerunning, setRerunning] = useState(false);
  const a = card.assessment;

  const rerun = useCallback(async () => {
    setRerunning(true);
    try {
      await runSalesAssessment(card.project_id);
      // 日本チェックが非同期のため、数回ポーリングして最新状態を取り込む。
      for (let i = 0; i < 8; i++) {
        await new Promise((r) => setTimeout(r, 2000));
        const fresh = await fetchProjectCopilotV2(card.project_id);
        onCardUpdate(fresh);
        if (fresh.assessment_state !== "checking_japan") break;
      }
    } catch {
      // 失敗しても画面は壊さない（状態はそのまま）。
    } finally {
      setRerunning(false);
    }
  }, [card.project_id, onCardUpdate]);

  return (
    <div className="border-b border-slate-100">
      <div className="flex items-center gap-3 px-4 py-3">
        <button
          onClick={() => setOpen((o) => !o)}
          className="w-5 text-slate-400 hover:text-slate-700"
          aria-label="詳細"
        >
          {open ? "▾" : "▸"}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className={`rounded px-1.5 py-0.5 text-[10px] ${siteColor(card.source_site)}`}>
              {siteLabel(card.source_site)}
            </span>
            <span className="truncate font-medium text-slate-800" title={card.title}>
              {card.title}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-slate-500">
            <span>{card.maker_name ?? "メーカー不明"}</span>
            <span
              className={`rounded px-1 py-0.5 text-[10px] ${STATE_COLORS[card.assessment_state]}`}
            >
              {STATE_LABELS[card.assessment_state]}
            </span>
            {card.tags.map((t) => (
              <span key={t} className="rounded bg-emerald-50 px-1 py-0.5 text-[10px] text-emerald-700">
                {t}
              </span>
            ))}
          </div>
        </div>
        {/* 総合優先度（未評価は 0 点/grade E にせず「未評価」表示） */}
        <div className="flex flex-col items-center">
          <span className="text-[10px] text-slate-400">総合</span>
          {card.priority_score == null ? (
            <span className="text-xs font-medium text-slate-400">未評価</span>
          ) : (
            <span className="flex items-center gap-1">
              <span className="text-sm font-bold text-slate-900">{card.priority_score}</span>
              {card.priority_grade && (
                <span className={`rounded px-1 text-[10px] font-bold ${GRADE_COLORS[card.priority_grade]}`}>
                  {card.priority_grade}
                </span>
              )}
            </span>
          )}
        </div>
        <ScoreBadge score={a.japan_market_fit.score} grade={a.japan_market_fit.grade} label="日本適性" />
        <ScoreBadge score={a.exclusivity.score} grade={a.exclusivity.grade} label="独占" />
        <ScoreBadge score={a.makuake_fit.score} grade={a.makuake_fit.grade} label="Makuake" />
        <div className="flex flex-col items-center">
          <span className="text-[10px] text-slate-400">確度</span>
          <span className="text-sm text-slate-700">{a.confidence ?? "-"}</span>
        </div>
        <div className="w-24 text-right">
          <DecisionBadge decision={card.decision} />
        </div>
      </div>
      {open && <CardDetail card={card} onRerun={rerun} rerunning={rerunning} />}
    </div>
  );
}

export default function SalesCopilotV2Page() {
  const [data, setData] = useState<SalesCopilotV2Dashboard | null>(null);
  const [cards, setCards] = useState<SalesCopilotV2Card[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<V2FilterKey>("all");
  const [sort, setSort] = useState<V2SortKey>("priority");

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await fetchSalesCopilotV2(20);
      setData(d);
      setCards(d.items);
    } catch (e) {
      setError(e instanceof Error ? e.message : "読み込みに失敗しました");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const updateCard = useCallback((c: SalesCopilotV2Card) => {
    setCards((prev) => prev.map((x) => (x.project_id === c.project_id ? c : x)));
  }, []);

  const visible = useMemo(
    () => sortCards(cards.filter((c) => matchesFilter(c, filter)), sort),
    [cards, filter, sort]
  );

  const filterKeys: V2FilterKey[] = [
    "all",
    "sell_now_exclusive",
    "needs_email",
    "needs_contact",
    "data_insufficient",
    "deprioritize",
    "high_japan_fit",
    "high_exclusivity",
    "makuake_promising",
    "japan_not_checked",
    "low_confidence",
  ];

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-6xl px-6 py-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">営業AI秘書 (Sales Copilot v2)</h1>
            <p className="text-sm text-slate-500">
              日本市場適性・独占販売可能性・Makuake適性を統合し、次の一手を提示します（ルールベース）。
            </p>
          </div>
          <button
            onClick={load}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-white"
          >
            再読み込み
          </button>
        </div>

        {data && (
          <div className="mb-3 flex flex-wrap gap-3 text-xs text-slate-600">
            <span>対象 {data.scanned} 件</span>
            <span>日本チェック未実施 {data.summary_counts.japan_not_checked} 件</span>
            <span>チェック中 {data.summary_counts.checking_japan} 件</span>
            <span>データ不足 {data.summary_counts.data_insufficient} 件</span>
            <span>confidence低 {data.summary_counts.low_confidence} 件</span>
          </div>
        )}

        {/* フィルター */}
        <div className="mb-3 flex flex-wrap gap-1.5">
          {filterKeys.map((k) => (
            <button
              key={k}
              onClick={() => setFilter(k)}
              className={`rounded-full px-2.5 py-1 text-xs ${
                filter === k
                  ? "bg-slate-800 text-white"
                  : "border border-slate-300 bg-white text-slate-600 hover:bg-slate-100"
              }`}
            >
              {FILTER_LABELS[k]}
            </button>
          ))}
        </div>

        {/* 並び替え */}
        <div className="mb-3 flex items-center gap-2 text-sm">
          <span className="text-slate-500">並び替え:</span>
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as V2SortKey)}
            className="rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {(Object.keys(SORT_LABELS) as V2SortKey[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABELS[k]}
              </option>
            ))}
          </select>
          <span className="text-slate-400">表示 {visible.length} 件</span>
        </div>

        {loading && (
          <div className="rounded border border-slate-200 bg-white p-8 text-center text-slate-400">
            読み込み中…
          </div>
        )}
        {error && !loading && (
          <div className="rounded border border-rose-200 bg-rose-50 p-4 text-sm text-rose-700">
            エラー: {error}
            <button onClick={load} className="ml-3 underline">
              再試行
            </button>
          </div>
        )}
        {!loading && !error && visible.length === 0 && (
          <div className="rounded border border-slate-200 bg-white p-8 text-center text-slate-400">
            該当する案件がありません。
          </div>
        )}
        {!loading && !error && visible.length > 0 && (
          <div className="rounded border border-slate-200 bg-white">
            {visible.map((c) => (
              <V2Row key={c.project_id} card={c} onCardUpdate={updateCard} />
            ))}
          </div>
        )}
      </main>
    </div>
  );
}
