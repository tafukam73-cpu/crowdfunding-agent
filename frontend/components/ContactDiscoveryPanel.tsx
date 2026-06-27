"use client";

import { useEffect, useState } from "react";

import {
  applyDiscoveryToCrm,
  type ContactDiscovery,
  fetchContactDiscovery,
  formatDateTime,
  runContactDiscovery,
} from "@/lib/api";

const TIER_COLORS: Record<string, string> = {
  high: "bg-emerald-100 text-emerald-700",
  mid: "bg-amber-100 text-amber-700",
  low: "bg-slate-100 text-slate-600",
  other: "bg-sky-100 text-sky-700",
};

const SOCIAL_LABELS: { key: keyof ContactDiscovery; label: string }[] = [
  { key: "instagram_url", label: "Instagram" },
  { key: "facebook_url", label: "Facebook" },
  { key: "twitter_url", label: "X / Twitter" },
  { key: "linkedin_url", label: "LinkedIn" },
  { key: "youtube_url", label: "YouTube" },
];

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
    >
      {copied ? "コピーしました" : "コピー"}
    </button>
  );
}

export default function ContactDiscoveryPanel({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged?: () => void;
}) {
  const [data, setData] = useState<ContactDiscovery | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchContactDiscovery(projectId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    setApplyMsg(null);
    try {
      setData(await runContactDiscovery(projectId));
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onApply(email: string) {
    setApplyMsg(null);
    try {
      const r = await applyDiscoveryToCrm(projectId, email);
      setApplyMsg(`CRMに反映しました：${r.email}（担当者を追加）`);
      onChanged?.();
    } catch (e) {
      setApplyMsg(`反映に失敗しました：${String(e)}`);
    }
  }

  const failed = data?.status === "failed";
  const completed = data?.status === "completed";
  const socials = SOCIAL_LABELS.filter((s) => data?.[s.key]);

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">連絡先探索</h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "探索中…" : data ? "再探索" : "連絡先を探索"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        公式サイト・問い合わせページ・SNS から営業先候補（メール／フォーム／SNS）を収集します。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {!data && !error && (
        <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
          まだ探索されていません。「連絡先を探索」を押すと、公式サイト等から連絡先候補を収集します。
        </p>
      )}

      {failed && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
          <p className="font-semibold">探索に失敗しました。</p>
          <p className="mt-1 whitespace-pre-wrap break-all text-xs">
            {data?.error ?? "原因不明のエラーです。"}
          </p>
        </div>
      )}

      {completed && data && (
        <div className="mt-3 space-y-4 rounded-lg border border-slate-200 bg-white p-5 text-sm">
          {/* 代表値 */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-semibold text-slate-500">確度</span>
            <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
              {data.confidence_score ?? 0} / 100
            </span>
          </div>

          <div>
            <p className="text-xs font-semibold text-slate-500">代表メール</p>
            {data.primary_email ? (
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-900">
                  {data.primary_email}
                </span>
                <CopyButton text={data.primary_email} />
                <button
                  onClick={() => onApply(data.primary_email!)}
                  className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                >
                  CRMに反映
                </button>
              </div>
            ) : (
              <p className="mt-1 text-slate-400">見つかりませんでした</p>
            )}
          </div>

          {data.primary_contact_form_url && (
            <div>
              <p className="text-xs font-semibold text-slate-500">問い合わせフォーム</p>
              <a
                href={data.primary_contact_form_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block break-all text-blue-700 hover:underline"
              >
                {data.primary_contact_form_url} ↗
              </a>
            </div>
          )}

          {data.official_site_url && (
            <div>
              <p className="text-xs font-semibold text-slate-500">公式サイト</p>
              <a
                href={data.official_site_url}
                target="_blank"
                rel="noreferrer"
                className="mt-1 inline-block break-all text-blue-700 hover:underline"
              >
                {data.official_site_url} ↗
              </a>
            </div>
          )}

          {socials.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">SNS</p>
              <div className="mt-1 flex flex-wrap gap-3">
                {socials.map((s) => (
                  <a
                    key={s.key}
                    href={data[s.key] as string}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-700 hover:underline"
                  >
                    {s.label} ↗
                  </a>
                ))}
              </div>
            </div>
          )}

          {/* 発見メール一覧（スコア順） */}
          {data.discovered_emails && data.discovered_emails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">
                発見したメール（優先度順）
              </p>
              <ul className="mt-1 space-y-1">
                {data.discovered_emails.map((e) => (
                  <li
                    key={e.email}
                    className="flex flex-wrap items-center gap-2"
                  >
                    <span
                      className={`rounded px-2 py-0.5 text-xs font-medium ${
                        TIER_COLORS[e.tier] ?? TIER_COLORS.other
                      }`}
                    >
                      {e.tier} {e.score}
                    </span>
                    <span className="text-slate-800">{e.email}</span>
                    <CopyButton text={e.email} />
                    <button
                      onClick={() => onApply(e.email)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      CRMに反映
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {applyMsg && (
            <p className="rounded-md border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
              {applyMsg}
            </p>
          )}

          {/* 探索済み URL */}
          {data.searched_urls && data.searched_urls.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                探索済み URL（{data.searched_urls.length}）
              </summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {data.searched_urls.map((u, i) => (
                  <li key={i} className="break-all">
                    {u}
                  </li>
                ))}
              </ul>
            </details>
          )}

          <p className="text-right text-xs text-slate-400">
            {data.notes ? `${data.notes} ・ ` : ""}
            {formatDateTime(data.updated_at)}
          </p>
        </div>
      )}
    </div>
  );
}
