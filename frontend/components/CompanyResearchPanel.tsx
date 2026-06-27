"use client";

import { useEffect, useState } from "react";

import {
  fetchCompanyResearch,
  formatDateTime,
  runCompanyResearch,
  type CompanyResearch,
} from "@/lib/api";

function List({ items }: { items: string[] | null | undefined }) {
  if (!items || items.length === 0) return <span className="text-slate-400">—</span>;
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div>
      <p className="text-xs font-semibold text-slate-500">{label}</p>
      <div className="mt-0.5 text-sm text-slate-800">{children}</div>
    </div>
  );
}

export default function CompanyResearchPanel({
  projectId,
  onResearched,
}: {
  projectId: number;
  onResearched?: () => void;
}) {
  const [research, setResearch] = useState<CompanyResearch | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    fetchCompanyResearch(projectId)
      .then(setResearch)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      const r = await runCompanyResearch(projectId);
      setResearch(r);
      onResearched?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const failed = research?.research_status === "failed";
  const completed = research?.research_status === "completed";

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">AI 企業リサーチ</h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy
            ? "リサーチ中…"
            : research
            ? "再リサーチ"
            : "企業リサーチを実行"}
        </button>
      </div>

      <p className="mt-1 text-xs text-slate-400">
        メーカー公式サイトや案件情報をもとに、会社・商品を整理して営業メールに反映します。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {!research && !error && (
        <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
          まだリサーチされていません。「企業リサーチを実行」を押すと、会社・商品の要約や営業の角度を生成します。
        </p>
      )}

      {failed && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-semibold">リサーチに失敗しました。</p>
          <p className="mt-1 whitespace-pre-wrap break-all text-xs">
            {research?.raw_notes ?? "原因不明のエラーです。"}
          </p>
          <p className="mt-2 text-xs text-red-500">
            「再リサーチ」で再実行できます（ANTHROPIC_API_KEY 未設定時はモックで動作します）。
          </p>
        </div>
      )}

      {completed && research && (
        <div className="mt-3 space-y-3 rounded-lg border border-slate-200 bg-white p-5">
          <Field label="会社・ブランド要約">
            {research.brand_summary ?? "—"}
          </Field>
          {research.company_mission && (
            <Field label="ミッション">{research.company_mission}</Field>
          )}
          {research.product_summary && (
            <Field label="商品概要">{research.product_summary}</Field>
          )}
          <Field label="商品の魅力（特徴）">
            <List items={research.key_product_features} />
          </Field>
          <Field label="強み">
            <List items={research.brand_strengths} />
          </Field>
          <Field label="差別化ポイント">
            <List items={research.differentiation_points} />
          </Field>
          <Field label="個別称賛ポイント">
            <span className="rounded bg-amber-50 px-1 py-0.5 text-amber-900">
              {research.personalized_compliment ?? "—"}
            </span>
          </Field>
          <Field label="日本市場での訴求ポイント">
            {research.japan_market_fit ?? "—"}
          </Field>
          <Field label="営業メールで使うべき角度">
            <List items={research.outreach_angles} />
          </Field>
          <Field label="注意点（避けるべき表現など）">
            <List items={research.risks_or_cautions} />
          </Field>
          <Field label="参照元 URL">
            {research.sources && research.sources.length > 0 ? (
              <ul className="list-disc space-y-0.5 pl-4">
                {research.sources.map((s, i) => (
                  <li key={i}>
                    {s.startsWith("http") ? (
                      <a
                        href={s}
                        target="_blank"
                        rel="noreferrer"
                        className="break-all text-blue-700 hover:underline"
                      >
                        {s}
                      </a>
                    ) : (
                      <span className="text-slate-500">{s}</span>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <span className="text-slate-400">—</span>
            )}
          </Field>

          <p className="text-right text-xs text-slate-400">
            {research.model} ・ {formatDateTime(research.updated_at)}
          </p>
        </div>
      )}
    </div>
  );
}
