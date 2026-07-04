"use client";

import { useState } from "react";

import {
  formatDateYmd,
  mockSalesActivities,
  SALES_ACTIVITY_STATUS_HINT,
  SALES_ACTIVITY_TYPE_COLORS,
  SALES_ACTIVITY_TYPE_LABELS,
  SALES_ACTIVITY_TYPE_ORDER,
  SALES_MAKER_STATUS_COLORS,
  SALES_MAKER_STATUS_LABELS,
  type SalesActivity,
  type SalesActivityType,
} from "@/lib/api";

// 営業活動を時系列で管理するタイムライン。
// 「いつ・何をしたか・次に何をするか」を 1 枚のカードで俯瞰できる。
// 追加はローカル state に即時反映（DB 未保存）。初期表示はモックデータ。

function ActivityTypeBadge({ type }: { type: SalesActivityType }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${SALES_ACTIVITY_TYPE_COLORS[type]}`}
    >
      {SALES_ACTIVITY_TYPE_LABELS[type]}
    </span>
  );
}

// 追加フォームの初期値。
const EMPTY_FORM: {
  type: SalesActivityType;
  title: string;
  content: string;
  next_action: string;
} = {
  type: "research",
  title: "",
  content: "",
  next_action: "",
};

export default function SalesActivityTimeline({
  makerId,
  initialActivities,
}: {
  makerId?: number | string;
  // 省略時はモックデータで初期化する。
  initialActivities?: SalesActivity[];
}) {
  // 表示は新しい日付が上（降順）。同日はモックの並び（配列順）を維持する。
  const [activities, setActivities] = useState<SalesActivity[]>(
    () => initialActivities ?? mockSalesActivities()
  );
  const [form, setForm] = useState({ ...EMPTY_FORM });

  function set<K extends keyof typeof EMPTY_FORM>(
    key: K,
    value: (typeof EMPTY_FORM)[K]
  ) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function onAdd() {
    if (!form.title.trim()) return;
    const item: SalesActivity = {
      id:
        typeof crypto !== "undefined" && "randomUUID" in crypto
          ? crypto.randomUUID()
          : `act-${Date.now()}`,
      date: formatDateYmd(),
      type: form.type,
      title: form.title.trim(),
      content: form.content.trim(),
      assignee: "自分",
      next_action: form.next_action.trim() || null,
      status: SALES_ACTIVITY_STATUS_HINT[form.type],
    };
    // 新しい活動を先頭に追加（即時反映）。
    setActivities((prev) => [item, ...prev]);
    setForm({ ...EMPTY_FORM });
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold text-slate-900">
          営業活動タイムライン
        </h2>
        <span className="text-xs text-slate-500">{activities.length} 件</span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        いつ・何をしたか・次に何をするかを時系列で管理します。※ 追加内容はこの画面のみ
        （サーバー未保存）。
      </p>

      {/* 追加フォーム */}
      <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-4">
        <h3 className="text-sm font-semibold text-slate-800">活動を追加</h3>
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          <label className="flex flex-col text-xs text-slate-500">
            活動タイプ
            <select
              className="input mt-1"
              value={form.type}
              onChange={(e) =>
                set("type", e.target.value as SalesActivityType)
              }
            >
              {SALES_ACTIVITY_TYPE_ORDER.map((t) => (
                <option key={t} value={t}>
                  {SALES_ACTIVITY_TYPE_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col text-xs text-slate-500">
            タイトル
            <input
              className="input mt-1"
              value={form.title}
              onChange={(e) => set("title", e.target.value)}
              placeholder="例：担当者へ初回メール送信"
            />
          </label>
        </div>
        <label className="mt-3 flex flex-col text-xs text-slate-500">
          内容
          <textarea
            className="input mt-1 min-h-[60px]"
            value={form.content}
            onChange={(e) => set("content", e.target.value)}
            placeholder="活動の詳細（任意）"
          />
        </label>
        <label className="mt-3 flex flex-col text-xs text-slate-500">
          次回アクション
          <input
            className="input mt-1"
            value={form.next_action}
            onChange={(e) => set("next_action", e.target.value)}
            placeholder="例：3営業日返信が無ければフォローアップ"
          />
        </label>
        <button
          onClick={onAdd}
          disabled={!form.title.trim()}
          className="mt-3 rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          活動を追加
        </button>
      </div>

      {/* タイムライン */}
      <ol className="mt-5 space-y-4">
        {activities.map((a, i) => (
          <li key={a.id} className="relative flex gap-3">
            {/* 縦線 + ドット */}
            <div className="flex flex-col items-center">
              <span className="mt-1 h-2.5 w-2.5 rounded-full bg-slate-400" />
              {i < activities.length - 1 && (
                <span className="mt-1 w-px flex-1 bg-slate-200" />
              )}
            </div>

            <div className="flex-1 rounded-md border border-slate-200 p-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs font-medium text-slate-500">
                  {a.date}
                </span>
                <ActivityTypeBadge type={a.type} />
                {a.status && (
                  <span
                    className={`rounded px-2 py-0.5 text-xs font-medium ${SALES_MAKER_STATUS_COLORS[a.status]}`}
                  >
                    {SALES_MAKER_STATUS_LABELS[a.status]}
                  </span>
                )}
                {a.assignee && (
                  <span className="text-xs text-slate-400">
                    担当：{a.assignee}
                  </span>
                )}
              </div>

              <p className="mt-1 text-sm font-semibold text-slate-900">
                {a.title}
              </p>
              {a.content && (
                <p className="mt-1 whitespace-pre-wrap text-xs text-slate-600">
                  {a.content}
                </p>
              )}
              {a.next_action && (
                <p className="mt-2 text-xs text-slate-600">
                  <span className="font-medium text-slate-700">
                    次回アクション：
                  </span>
                  {a.next_action}
                </p>
              )}
            </div>
          </li>
        ))}
        {activities.length === 0 && (
          <p className="text-sm text-slate-400">
            営業活動はまだありません。上のフォームから追加してください。
          </p>
        )}
      </ol>
    </section>
  );
}
