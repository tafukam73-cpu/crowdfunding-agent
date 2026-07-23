"use client";

import FactChips from "@/components/FactChips";
import CampaignLink from "@/components/CampaignLink";
import Link from "next/link";
import { useEffect, useState } from "react";

import {
  COPILOT_DECISION_COLORS,
  type CopilotAction,
  type CopilotCard,
  type CopilotDashboard,
  fetchSalesCopilot,
  formatMoney,
} from "@/lib/api";

// アクションボタンのキー → 表示ラベル・遷移先・スタイル。
// 実処理は案件ページに集約する（?sales=1 で営業フローを開く）。
const ACTION_META: Record<
  CopilotAction,
  { label: string; href: (id: number) => string; primary?: boolean }
> = {
  email: {
    label: "営業メール作成",
    href: (id) => `/projects/${id}?sales=1`,
    primary: true,
  },
  contact_intelligence: {
    label: "Contact Intelligence",
    href: (id) => `/projects/${id}?sales=1`,
    primary: true,
  },
  company_research: {
    label: "企業リサーチ",
    href: (id) => `/projects/${id}?sales=1`,
  },
  followup: {
    label: "フォローアップ作成",
    href: (id) => `/projects/${id}?sales=1`,
    primary: true,
  },
  change_status: {
    label: "ステータス変更",
    href: (id) => `/projects/${id}?sales=1`,
  },
  add_crm: { label: "CRMへ追加", href: (id) => `/projects/${id}` },
  open: { label: "案件を開く", href: (id) => `/projects/${id}` },
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

function DecisionBadge({ card }: { card: CopilotCard }) {
  const cls =
    COPILOT_DECISION_COLORS[card.decision] ?? "bg-slate-100 text-slate-600";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-bold ${cls}`}>
      {card.decision_label}
    </span>
  );
}

function ActionButtons({ card }: { card: CopilotCard }) {
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5">
      {card.actions.map((a) => {
        const meta = ACTION_META[a];
        if (!meta) return null;
        const cls = meta.primary
          ? "rounded bg-emerald-600 px-2 py-0.5 text-[11px] font-bold text-white hover:bg-emerald-700"
          : "rounded border border-slate-300 px-2 py-0.5 text-[11px] font-medium text-slate-600 hover:bg-white";
        return (
          <Link key={a} href={meta.href(card.project_id)} className={cls}>
            {meta.label}
          </Link>
        );
      })}
    </div>
  );
}

// 1 案件のコパイロット・カード（サマリー＋判断＋理由＋アクション）。
function Card({ card, detailed = false }: { card: CopilotCard; detailed?: boolean }) {
  const f = card.summary.funding;
  return (
    <li className="rounded-md border border-slate-200 bg-white/80 px-2.5 py-2">
      <div className="flex items-start justify-between gap-2">
        <Link
          href={`/projects/${card.project_id}`}
          className="truncate text-sm font-medium text-slate-800 hover:text-blue-700 hover:underline"
        >
          {card.title}
        </Link>
        <div className="flex shrink-0 items-center gap-1.5">
          <CampaignLink source={card} />
          <DecisionBadge card={card} />
        </div>
      </div>

      <FactChips facts={card.facts} className="mt-1" />

      <div className="mt-0.5 flex flex-wrap items-center gap-x-2 gap-y-0.5 text-[11px]">
        {card.summary.japan_market_fit && (
          <span className="text-slate-400">
            日本相性: {card.summary.japan_market_fit}
          </span>
        )}
        {f.raised_amount != null && (
          <span className="text-slate-400">
            調達 {formatMoney(f.raised_amount, f.currency)}
            {f.rate_pct != null ? `・${f.rate_pct}%` : ""}
          </span>
        )}
      </div>

      {/* 次にやるべきこと */}
      <p className="mt-1 text-[11px] font-medium text-emerald-800">
        → {card.next_action}
      </p>

      {/* なぜそう判断したか（必ず表示） */}
      {card.reasons.length > 0 && (
        <ul className="mt-1 flex flex-wrap gap-1">
          {card.reasons.slice(0, detailed ? 5 : 3).map((r, i) => (
            <li
              key={i}
              className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-600"
            >
              {r}
            </li>
          ))}
        </ul>
      )}

      {/* 詳細（最重要アクション用）：会社/商品/連絡先/リスク */}
      {detailed && (
        <div className="mt-1.5 space-y-0.5 text-[11px] text-slate-500">
          {card.summary.company && <p>会社: {card.summary.company}</p>}
          {card.summary.contact_status && (
            <p>連絡先: {card.summary.contact_status}</p>
          )}
          {card.summary.risks.length > 0 && (
            <p className="text-rose-600">リスク: {card.summary.risks.join(" / ")}</p>
          )}
        </div>
      )}

      <ActionButtons card={card} />
    </li>
  );
}

// バケット（優先営業 / 連絡先探索 / … ）。
function Bucket({
  num,
  label,
  cls,
  items,
}: {
  num: string;
  label: string;
  cls: string;
  items: CopilotCard[];
}) {
  return (
    <div className={`rounded-lg border p-3 ${cls}`}>
      <p className="text-xs font-bold text-slate-700">
        {num} {label}
        <span className="ml-1 font-normal text-slate-400">{items.length}</span>
      </p>
      {items.length === 0 ? (
        <p className="mt-2 text-xs text-slate-400">なし</p>
      ) : (
        <ul className="mt-1.5 space-y-1.5">
          {items.map((c) => (
            <Card key={c.project_id} card={c} />
          ))}
        </ul>
      )}
    </div>
  );
}

export default function SalesCopilotPanel({ reloadKey }: { reloadKey?: number }) {
  const [data, setData] = useState<CopilotDashboard | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    fetchSalesCopilot(5)
      .then((d) => active && setData(d))
      .catch((e) => active && setError(String(e)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [reloadKey, retryKey]);

  return (
    <div className="rounded-xl border border-indigo-200 bg-gradient-to-br from-indigo-50 to-white p-5 shadow-sm">
      <h2 className="text-base font-bold text-indigo-900">🧭 AI営業コパイロット</h2>
      <p className="mt-0.5 text-xs text-slate-500">
        案件・企業リサーチ・Contact Intelligence・CRM・営業状況を横断し、「今どう動くべきか」を判断・理由付きで提案します。
      </p>

      {error && (
        <div className="mt-2 rounded border border-red-200 bg-red-50 p-2 text-sm text-red-700">
          <div>読み込み失敗：{error}</div>
          <div className="mt-1 text-xs text-red-500">
            バックエンド（http://localhost:8000）の稼働をご確認ください。
          </div>
          <button
            onClick={() => setRetryKey((k) => k + 1)}
            className="mt-1 rounded border border-red-300 bg-white px-2 py-0.5 text-xs text-red-700 hover:bg-red-100"
          >
            再取得
          </button>
        </div>
      )}
      {loading && !data && (
        <p className="mt-2 text-sm text-slate-400">分析中…</p>
      )}

      {data && (
        <div className="mt-3 space-y-3">
          {/* AI からのコメント */}
          <div className="rounded-lg border border-indigo-200 bg-white/70 px-3 py-2 text-sm text-indigo-900">
            💬 {data.ai_comment}
          </div>

          {/* 今日の最重要アクション */}
          {data.top_action && (
            <div className="rounded-lg border-2 border-emerald-300 bg-emerald-50/70 p-3">
              <p className="text-xs font-bold text-emerald-900">
                🎯 今日の最重要アクション
              </p>
              <ul className="mt-1.5">
                <Card card={data.top_action} detailed />
              </ul>
            </div>
          )}

          {/* バケット群 */}
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2 xl:grid-cols-3">
            <Bucket
              num="①"
              label="優先営業案件 TOP5"
              cls="border-emerald-300 bg-emerald-50"
              items={data.priority_sales}
            />
            <Bucket
              num="②"
              label="連絡先探索すべき案件"
              cls="border-cyan-300 bg-cyan-50"
              items={data.needs_contact}
            />
            <Bucket
              num="③"
              label="メール生成すべき案件"
              cls="border-sky-300 bg-sky-50"
              items={data.needs_email}
            />
            <Bucket
              num="④"
              label="フォローすべき案件"
              cls="border-amber-300 bg-amber-50"
              items={data.followup}
            />
            <Bucket
              num="⑤"
              label="データ不足案件"
              cls="border-orange-300 bg-orange-50"
              items={data.data_insufficient}
            />
            <Bucket
              num="⑥"
              label="見送り候補"
              cls="border-rose-300 bg-rose-50"
              items={data.drop_candidates}
            />
          </div>

          <p className="text-[11px] text-slate-400">
            {data.scanned} 件を分析（ルールベース判断・保存済みデータのみ）。各アクションは案件ページで実行します。
          </p>
        </div>
      )}
    </div>
  );
}
