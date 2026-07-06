"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  createFollowupEmail,
  fetchSalesTasks,
  type FollowUpLevel,
  type FollowupEmailResult,
  SALES_STATUS_LABELS,
  type SalesTask,
  type TodayTasks,
} from "@/lib/api";

// 「今日やること」の 5 グループ（営業アシスタント）。cta は主要アクションの文言。
const GROUPS: {
  key: keyof TodayTasks;
  num: string;
  label: string;
  cls: string;
  cta: string | null;
}[] = [
  {
    key: "to_contact",
    num: "①",
    label: "今日営業する案件",
    cls: "border-emerald-300 bg-emerald-50",
    cta: "営業メール作成",
  },
  {
    key: "followup",
    num: "②",
    label: "今日フォローする案件",
    cls: "border-amber-300 bg-amber-50",
    cta: "フォローアップメール作成",
  },
  {
    key: "replied",
    num: "③",
    label: "返信あり",
    cls: "border-indigo-300 bg-indigo-50",
    cta: "返信に対応",
  },
  {
    key: "negotiating",
    num: "④",
    label: "商談中",
    cls: "border-purple-300 bg-purple-50",
    cta: "商談を進める",
  },
  {
    key: "idle",
    num: "⑤",
    label: "放置でよい案件",
    cls: "border-slate-300 bg-slate-50",
    cta: null,
  },
];

const FOLLOWUP_BADGE: Record<FollowUpLevel, { label: string; cls: string }> = {
  normal: { label: "要フォロー", cls: "bg-amber-100 text-amber-800" },
  high: { label: "優先フォロー", cls: "bg-orange-200 text-orange-900" },
  final: { label: "最終フォロー", cls: "bg-rose-200 text-rose-900" },
};

function Stars({ n }: { n: number }) {
  const full = Math.max(0, Math.min(5, n));
  return (
    <span className="text-amber-500" aria-label={`優先度 ${full} / 5`}>
      {"★".repeat(full)}
      <span className="text-slate-300">{"☆".repeat(5 - full)}</span>
    </span>
  );
}

// フォローアップ専用アクション：その場でメール作成し Gmail 下書きを開けるようにする。
function FollowupAction({ task }: { task: SalesTask }) {
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<FollowupEmailResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    setBusy(true);
    setError(null);
    try {
      const res = await createFollowupEmail(task.project_id);
      setResult(res);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    return (
      <div className="mt-1.5 space-y-1">
        <p className="text-[11px] font-medium text-emerald-700">
          フォローアップ作成（{result.stage_label}）→ 返信待ちに更新
        </p>
        <div className="flex flex-wrap gap-1.5">
          <a
            href={result.gmail_compose_url}
            target="_blank"
            rel="noreferrer"
            className="rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white hover:bg-emerald-700"
          >
            Gmailで下書きを開く
          </a>
          <Link
            href={`/projects/${task.project_id}?sales=1`}
            className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-white"
          >
            案件を開く
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
      <button
        onClick={run}
        disabled={busy}
        className="rounded bg-amber-600 px-2 py-0.5 text-[11px] font-bold text-white hover:bg-amber-700 disabled:opacity-50"
      >
        {busy ? "作成中…" : "フォローアップメール作成"}
      </button>
      <Link
        href={`/projects/${task.project_id}?sales=1`}
        className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-white"
      >
        案件を開く
      </Link>
      {error && <span className="text-[11px] text-red-600">作成に失敗しました</span>}
    </div>
  );
}

// 案件詳細（営業フロー）への各種アクションボタン。実処理は案件ページに集約する。
function ActionButtons({
  task,
  cta,
}: {
  task: SalesTask;
  cta: string | null;
}) {
  const salesUrl = `/projects/${task.project_id}?sales=1`;
  const detailUrl = `/projects/${task.project_id}`;
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {cta && (
        <Link
          href={salesUrl}
          className="rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white hover:bg-emerald-700"
        >
          {cta}
        </Link>
      )}
      {/* 連絡先が未取得なら Contact Intelligence を促す */}
      {!task.has_contact && (
        <Link
          href={salesUrl}
          className="rounded border border-sky-300 px-2 py-0.5 text-[11px] font-medium text-sky-700 hover:bg-sky-50"
        >
          Contact Intelligence
        </Link>
      )}
      <Link
        href={salesUrl}
        className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-white"
      >
        ステータス変更
      </Link>
      <Link
        href={detailUrl}
        className="rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-white"
      >
        案件を開く
      </Link>
    </div>
  );
}

function TaskCard({
  task,
  cta,
  isFollowup,
}: {
  task: SalesTask;
  cta: string | null;
  isFollowup: boolean;
}) {
  const level = task.follow_up_level
    ? FOLLOWUP_BADGE[task.follow_up_level]
    : null;
  return (
    <li className="rounded-md border border-slate-200 bg-white/80 px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <Link
          href={`/projects/${task.project_id}`}
          className="truncate text-sm font-medium text-slate-800 hover:text-blue-700 hover:underline"
        >
          {task.title}
        </Link>
        <span className="shrink-0 text-[10px] text-slate-400">
          {SALES_STATUS_LABELS[task.sales_status]}
        </span>
      </div>

      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
        <Stars n={task.stars} />
        <span className="text-slate-500">営業価値 {task.priority_score}</span>
        {level && (
          <span className={`rounded px-1.5 py-0.5 font-bold ${level.cls}`}>
            {level.label}
          </span>
        )}
      </div>

      {task.reasons.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1">
          {task.reasons.slice(0, 4).map((r, i) => (
            <li
              key={i}
              className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600"
            >
              {r}
            </li>
          ))}
        </ul>
      )}

      {isFollowup ? (
        <FollowupAction task={task} />
      ) : (
        <ActionButtons task={task} cta={cta} />
      )}
    </li>
  );
}

export default function TodayTasksPanel({ reloadKey }: { reloadKey?: number }) {
  const [data, setData] = useState<TodayTasks | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setLoading(true);
    fetchSalesTasks(5)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey]);

  return (
    <div className="rounded-xl border border-rose-200 bg-gradient-to-br from-rose-50 to-white p-5 shadow-sm">
      <h2 className="text-base font-bold text-rose-900">🔥 今日やること</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        営業アシスタントが「今日営業・今日フォロー・返信対応・商談・放置可」を自動整理します。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">読み込み失敗：{error}</p>}
      {loading && !data && (
        <p className="mt-2 text-sm text-slate-400">読み込み中…</p>
      )}

      {data && (
        <div className="mt-3 grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
          {GROUPS.map((g) => {
            const items = data[g.key];
            return (
              <div key={g.key} className={`rounded-lg border p-3 ${g.cls}`}>
                <p className="text-xs font-bold text-slate-700">
                  {g.num} {g.label}
                  <span className="ml-1 font-normal text-slate-400">
                    {items.length}
                  </span>
                </p>
                {items.length === 0 ? (
                  <p className="mt-2 text-xs text-slate-400">なし</p>
                ) : (
                  <ul className="mt-1.5 space-y-1.5">
                    {items.map((t) => (
                      <TaskCard
                        key={t.project_id}
                        task={t}
                        cta={g.cta}
                        isFollowup={g.key === "followup"}
                      />
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
