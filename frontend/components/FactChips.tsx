"use client";

/**
 * カード・一覧に出す「確認可能な事実」のチップ列。
 *
 * 予測値（営業価値スコア・★・適性点数）の代わりにこれを表示する。値が取得できて
 * いない項目は推測で埋めず「未取得」と出す。
 */
import type { CompactFacts } from "@/lib/api";

function Chip({ label, value }: { label: string; value: string | null }) {
  return (
    <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-1.5 py-0.5 text-[11px] text-slate-600">
      <span className="text-slate-400">{label}</span>
      <span className={value ? "font-medium text-slate-800" : "text-slate-400"}>
        {value ?? "未取得"}
      </span>
    </span>
  );
}

export default function FactChips({
  facts,
  className = "",
}: {
  facts: CompactFacts | null | undefined;
  className?: string;
}) {
  if (!facts) return null;
  const state =
    facts.campaign_state === "募集中" && facts.days_remaining != null
      ? `募集中（残り${facts.days_remaining}日）`
      : facts.campaign_state;
  return (
    <div className={`flex flex-wrap items-center gap-1 ${className}`}>
      <Chip
        label="支援率"
        value={facts.funding_rate != null ? `${facts.funding_rate.toLocaleString()}%` : null}
      />
      <Chip
        label="支援者"
        value={
          facts.backers_count != null ? `${facts.backers_count.toLocaleString()}人` : null
        }
      />
      <Chip label="募集" value={state ?? null} />
      <Chip label="カテゴリ" value={facts.category ?? null} />
    </div>
  );
}
