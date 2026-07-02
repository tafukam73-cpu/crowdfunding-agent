"use client";

import { useCallback, useEffect, useState } from "react";

import Header from "@/components/Header";
import {
  fetchSalesOpportunities,
  formatDateTime,
  SALES_OPP_STATUS_COLORS,
  SALES_OPP_STATUS_LABELS,
  SALES_PRIORITY_LABELS,
  type SalesOpportunity,
  type SalesOpportunityStatus,
  updateSalesOpportunity,
} from "@/lib/api";

// 推奨チャネルの日本語表示。
const CHANNEL_LABELS: Record<string, string> = {
  email: "メール",
  contact_form: "問い合わせフォーム",
  linkedin: "LinkedIn",
  instagram: "Instagram",
  facebook: "Facebook",
  press: "Press / Media",
  distributor_page: "Wholesale / Distributor",
  pdf: "PDF資料",
  manual_research: "手動リサーチ",
};

const STATUS_ORDER: SalesOpportunityStatus[] = [
  "not_started",
  "researched",
  "contacted",
  "waiting_reply",
  "meeting",
  "negotiating",
  "likely_contract",
  "lost",
  "on_hold",
];

const PRIORITY_COLORS: Record<string, string> = {
  high: "bg-rose-100 text-rose-700",
  medium: "bg-amber-100 text-amber-700",
  low: "bg-slate-100 text-slate-500",
};

function PriorityBadge({ priority }: { priority: string | null }) {
  if (!priority) return <span className="text-slate-400">-</span>;
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        PRIORITY_COLORS[priority] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {SALES_PRIORITY_LABELS[priority] ?? priority}
    </span>
  );
}

// 1 行の編集フォーム（次アクション・期限・メモ）。
function EditRow({
  opp,
  onSaved,
}: {
  opp: SalesOpportunity;
  onSaved: (o: SalesOpportunity) => void;
}) {
  const [nextAction, setNextAction] = useState(opp.next_action ?? "");
  const [due, setDue] = useState(opp.next_action_due_date ?? "");
  const [notes, setNotes] = useState(opp.notes ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateSalesOpportunity(opp.id, {
        next_action: nextAction || null,
        next_action_due_date: due || null,
        notes: notes || null,
      });
      onSaved(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="space-y-2 rounded-md border border-slate-200 bg-slate-50 p-3">
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
        <label className="text-xs text-slate-600">
          次アクション
          <input
            value={nextAction}
            onChange={(e) => setNextAction(e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="例：サンプル送付、フォローメール"
          />
        </label>
        <label className="text-xs text-slate-600">
          フォローアップ期限
          <input
            type="date"
            value={due ?? ""}
            onChange={(e) => setDue(e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
      </div>
      <label className="block text-xs text-slate-600">
        メモ
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={2}
          className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          placeholder="商談メモ・担当者など"
        />
      </label>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <button
        onClick={save}
        disabled={busy}
        className="rounded bg-slate-900 px-3 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {busy ? "保存中…" : "保存"}
      </button>
    </div>
  );
}

function OpportunityRow({
  opp,
  onChanged,
}: {
  opp: SalesOpportunity;
  onChanged: (o: SalesOpportunity) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [savingStatus, setSavingStatus] = useState(false);

  async function changeStatus(status: SalesOpportunityStatus) {
    setSavingStatus(true);
    try {
      onChanged(await updateSalesOpportunity(opp.id, { status }));
    } catch {
      /* エラー時は据え置き（画面は固めない） */
    } finally {
      setSavingStatus(false);
    }
  }

  return (
    <div className="border-b border-slate-100 py-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <div className="min-w-[10rem] flex-1">
          <p className="font-medium text-slate-900">
            {opp.company_name || <span className="text-slate-400">企業名なし</span>}
          </p>
          <p className="text-xs text-slate-500">{opp.product_name || "-"}</p>
          {opp.website_url && (
            <a
              href={opp.website_url}
              target="_blank"
              rel="noreferrer"
              className="text-xs text-blue-600 hover:underline"
            >
              {opp.website_url} ↗
            </a>
          )}
        </div>

        <div className="text-center">
          <p className="text-[10px] text-slate-400">スコア</p>
          <p className="text-lg font-bold text-slate-800">{opp.sales_score ?? "-"}</p>
        </div>
        <div className="text-center">
          <p className="text-[10px] text-slate-400">優先度</p>
          <PriorityBadge priority={opp.sales_priority} />
        </div>
        <div className="text-center">
          <p className="text-[10px] text-slate-400">推奨チャネル</p>
          <p className="text-xs text-slate-700">
            {opp.recommended_channel
              ? CHANNEL_LABELS[opp.recommended_channel] ?? opp.recommended_channel
              : "-"}
          </p>
        </div>

        <div>
          <p className="text-[10px] text-slate-400">ステータス</p>
          <select
            value={opp.status}
            disabled={savingStatus}
            onChange={(e) => changeStatus(e.target.value as SalesOpportunityStatus)}
            className={`mt-0.5 rounded px-2 py-1 text-xs font-medium ${
              SALES_OPP_STATUS_COLORS[opp.status] ?? "bg-slate-100 text-slate-600"
            }`}
          >
            {STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {SALES_OPP_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </div>

        <button
          onClick={() => setEditing((v) => !v)}
          className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
        >
          {editing ? "閉じる" : "編集"}
        </button>
      </div>

      {/* 概要行（次アクション・期限・メモ・連絡先） */}
      <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-600">
        <span>
          次アクション:{" "}
          <span className="text-slate-800">{opp.next_action || "-"}</span>
        </span>
        <span>
          期限:{" "}
          <span
            className={
              opp.next_action_due_date ? "text-slate-800" : "text-slate-400"
            }
          >
            {opp.next_action_due_date || "未設定"}
          </span>
        </span>
        {opp.primary_email && (
          <span>
            連絡先: <span className="text-slate-800">{opp.primary_email}</span>
          </span>
        )}
        {opp.notes && (
          <span className="truncate">
            メモ: <span className="text-slate-700">{opp.notes}</span>
          </span>
        )}
      </div>

      {editing && (
        <div className="mt-2">
          <EditRow
            opp={opp}
            onSaved={(o) => {
              onChanged(o);
              setEditing(false);
            }}
          />
        </div>
      )}
    </div>
  );
}

export default function SalesOpportunitiesPage() {
  const [items, setItems] = useState<SalesOpportunity[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const [status, setStatus] = useState<string>("");
  const [priority, setPriority] = useState<string>("");
  const [sort, setSort] = useState<"score" | "due_date">("score");

  const load = useCallback(() => {
    setLoading(true);
    fetchSalesOpportunities({
      status: status || undefined,
      sales_priority: priority || undefined,
      sort,
    })
      .then((d) => {
        setItems(d);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [status, priority, sort]);

  useEffect(() => {
    load();
  }, [load]);

  function onChanged(updated: SalesOpportunity) {
    setItems((prev) => prev.map((o) => (o.id === updated.id ? updated : o)));
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="flex items-center justify-between">
          <h1 className="text-xl font-bold text-slate-900">営業案件管理</h1>
          <span className="text-sm text-slate-500">{items.length} 件</span>
        </div>
        <p className="mt-1 text-sm text-slate-500">
          Contact Intelligence の結果から作成した営業案件を、ステータス・次アクション・
          期限・メモで管理します。
        </p>

        {/* フィルター */}
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg border border-slate-200 bg-white p-3">
          <label className="text-xs text-slate-600">
            ステータス
            <select
              value={status}
              onChange={(e) => setStatus(e.target.value)}
              className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">すべて</option>
              {STATUS_ORDER.map((s) => (
                <option key={s} value={s}>
                  {SALES_OPP_STATUS_LABELS[s]}
                </option>
              ))}
            </select>
          </label>
          <label className="text-xs text-slate-600">
            優先度
            <select
              value={priority}
              onChange={(e) => setPriority(e.target.value)}
              className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="">すべて</option>
              <option value="high">高</option>
              <option value="medium">中</option>
              <option value="low">低</option>
            </select>
          </label>
          <label className="text-xs text-slate-600">
            並び順
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value as "score" | "due_date")}
              className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
            >
              <option value="score">営業スコア順</option>
              <option value="due_date">フォローアップ期限順</option>
            </select>
          </label>
          <button
            onClick={load}
            className="ml-auto rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            更新
          </button>
        </div>

        {error && <p className="mt-4 text-sm text-red-600">{error}</p>}

        {/* 一覧 */}
        <div className="mt-4 rounded-lg border border-slate-200 bg-white px-4">
          {loading ? (
            <p className="py-8 text-center text-sm text-slate-400">読み込み中…</p>
          ) : items.length === 0 ? (
            <p className="py-8 text-center text-sm text-slate-400">
              営業案件がありません。案件詳細の Contact Intelligence
              で「＋ 営業案件に追加」を押すと、ここに表示されます。
            </p>
          ) : (
            items.map((o) => (
              <OpportunityRow key={o.id} opp={o} onChanged={onChanged} />
            ))
          )}
        </div>

        {items.length > 0 && (
          <p className="mt-2 text-right text-[11px] text-slate-400">
            最終更新: {formatDateTime(items[0].updated_at)}
          </p>
        )}
      </main>
    </div>
  );
}
