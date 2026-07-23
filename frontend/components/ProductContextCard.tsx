"use client";

/**
 * 「いま何の商品を調査しているのか」を示すカード。
 *
 * メール探索（Contact Intelligence）・営業メール作成の各画面の先頭に置き、商品名・
 * 日本語概要・主な特徴3点・取得元・商品ページ/公式サイト・日本クラファン適性スコアと
 * 判定理由・メール探索を実行した理由をまとめて表示する。
 */
import { useEffect, useState } from "react";

import CampaignLink from "@/components/CampaignLink";
import {
  fetchContactSearchGate,
  SITE_LABELS,
  type ProductContext,
  type SourceSite,
} from "@/lib/api";

export default function ProductContextCard({
  projectId,
  reloadKey = 0,
}: {
  projectId: number;
  reloadKey?: number;
}) {
  const [ctx, setCtx] = useState<ProductContext | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchContactSearchGate(projectId)
      .then((d) => active && setCtx(d.product))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [projectId, reloadKey]);

  if (error) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-500">
        商品情報を取得できませんでした（{error}）
      </div>
    );
  }
  if (!ctx) {
    return (
      <div className="rounded-lg border border-slate-200 bg-white p-3 text-xs text-slate-400">
        商品情報を読み込み中…
      </div>
    );
  }

  const siteLabel = SITE_LABELS[ctx.source_site as SourceSite] ?? ctx.source_site;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="text-[11px] font-semibold text-slate-400">調査対象の商品</p>
          <h3 className="text-base font-bold text-slate-900">{ctx.product_name}</h3>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            {siteLabel}
          </span>
          <CampaignLink source={ctx} />
          {ctx.official_site_url && (
            <a
              href={ctx.official_site_url}
              target="_blank"
              rel="noopener noreferrer"
              className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-700 hover:bg-slate-50"
            >
              メーカー公式 ↗
            </a>
          )}
        </div>
      </div>

      {/* 日本語の商品概要 */}
      <p className="mt-3 whitespace-pre-wrap text-sm text-slate-700">
        {ctx.summary_ja ?? (
          <span className="text-amber-700">
            商品概要を生成できていません（商品内容が判別できないためメール探索は開始できません）
          </span>
        )}
      </p>

      {/* 主な特徴3点 */}
      {ctx.key_features.length > 0 && (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-xs text-slate-600">
          {ctx.key_features.map((f) => (
            <li key={f}>{f}</li>
          ))}
        </ul>
      )}

      {/* 適性スコアと判定理由 */}
      <div className="mt-3 rounded-md bg-slate-50 p-3 text-xs">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-semibold text-slate-600">日本クラファン適性</span>
          <span className="text-sm font-bold text-slate-900">
            {ctx.japan_crowdfunding_score ?? "—"}
          </span>
          <span
            className={
              ctx.eligible_for_contact_search
                ? "rounded bg-emerald-100 px-1.5 py-0.5 font-medium text-emerald-700"
                : "rounded bg-amber-100 px-1.5 py-0.5 font-medium text-amber-800"
            }
          >
            {ctx.eligible_for_contact_search ? "メール探索対象" : "メール探索対象外／要確認"}
          </span>
        </div>
        <p className="mt-1 text-slate-600">判定理由: {ctx.contact_search_gate_reason}</p>
        <p className="mt-0.5 text-slate-600">
          メール探索を実行した理由: {ctx.contact_search_rationale}
        </p>
        {ctx.gate_reasons.length > 0 && (
          <ul className="mt-1 list-disc space-y-0.5 pl-5 text-slate-500">
            {ctx.gate_reasons.map((r) => (
              <li key={r}>{r}</li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
