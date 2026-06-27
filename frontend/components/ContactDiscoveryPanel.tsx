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

const CHECKLIST_LABELS: Record<string, string> = {
  official_site_checked: "公式サイト確認",
  contact_page_found: "Contactページ発見",
  email_found: "メール発見",
  contact_form_found: "問い合わせフォーム発見",
  instagram_found: "Instagram発見",
  facebook_found: "Facebook発見",
  linkedin_found: "LinkedIn発見",
  press_page_found: "Press/Media発見",
  wholesale_page_found: "Wholesale発見",
  pdf_checked: "PDF確認",
  search_queries_generated: "検索クエリ生成",
};

const SOCIAL_LABELS: { key: keyof ContactDiscovery; label: string }[] = [
  { key: "instagram_url", label: "Instagram" },
  { key: "facebook_url", label: "Facebook" },
  { key: "twitter_url", label: "X / Twitter" },
  { key: "linkedin_url", label: "LinkedIn" },
  { key: "youtube_url", label: "YouTube" },
];

function CopyButton({ text, label }: { text: string; label?: string }) {
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
      {copied ? "コピーしました" : label ?? "コピー"}
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

  async function onApply(email?: string) {
    setApplyMsg(null);
    try {
      const r = await applyDiscoveryToCrm(projectId, email);
      setApplyMsg(
        r.email
          ? `CRMに反映しました：${r.email}（担当者を追加）`
          : "CRMに反映しました：推奨チャネル・連絡手段を営業履歴に記録しました。"
      );
      onChanged?.();
    } catch (e) {
      setApplyMsg(`反映に失敗しました：${String(e)}`);
    }
  }

  const failed = data?.status === "failed";
  const completed = data?.status === "completed";
  const socials = SOCIAL_LABELS.filter((s) => data?.[s.key]);
  const pdfs = (data?.approach_options ?? []).filter((o) => o.channel === "pdf");

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          連絡先探索（Contact Intelligence）
        </h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "探索中…" : data ? "再探索" : "連絡先を探索"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        メールが見つからなくても、問い合わせフォーム・SNS・PDF・検索クエリから最適な営業アプローチを提案します。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {!data && !error && (
        <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
          まだ探索されていません。「連絡先を探索」を押すと、営業可能な連絡手段を総合評価します。
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
          {/* スコア & 推奨 */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
              営業可能性スコア: {data.contactability_score ?? 0} / 100
            </span>
            {data.recommended_channel && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                推奨チャネル:{" "}
                {CHANNEL_LABELS[data.recommended_channel] ??
                  data.recommended_channel}
              </span>
            )}
          </div>

          {data.evidence_summary && (
            <div className="rounded-md border border-sky-200 bg-sky-50 p-3 text-xs text-sky-900">
              {data.evidence_summary}
            </div>
          )}

          {data.recommended_action && (
            <div>
              <p className="text-xs font-semibold text-slate-500">推奨アクション</p>
              <p className="mt-0.5 text-slate-800">{data.recommended_action}</p>
            </div>
          )}

          {/* CRM 反映（メールが無くても記録可能） */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              onClick={() => onApply(data.primary_email ?? undefined)}
              className="rounded border border-emerald-300 bg-emerald-50 px-2 py-1 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
            >
              CRMに反映（連絡手段を記録）
            </button>
            {applyMsg && (
              <span className="text-xs text-slate-600">{applyMsg}</span>
            )}
          </div>

          {/* 営業アプローチ候補 */}
          {data.approach_options && data.approach_options.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">
                営業アプローチ候補（スコア順）
              </p>
              <ul className="mt-1 space-y-1">
                {data.approach_options.map((o, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                      {o.score}
                    </span>
                    <span className="text-slate-800">{o.label}</span>
                    {o.url &&
                      (o.url.startsWith("mailto:") ? (
                        <CopyButton
                          text={o.url.replace("mailto:", "")}
                          label="メールをコピー"
                        />
                      ) : (
                        <a
                          href={o.url}
                          target="_blank"
                          rel="noreferrer"
                          className="text-blue-700 hover:underline"
                        >
                          開く ↗
                        </a>
                      ))}
                    {o.reason && (
                      <span className="text-xs text-slate-400">— {o.reason}</span>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 発見メール */}
          {data.discovered_emails && data.discovered_emails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">
                発見メール（優先度順）
              </p>
              <ul className="mt-1 space-y-1">
                {data.discovered_emails.map((e) => (
                  <li key={e.email} className="flex flex-wrap items-center gap-2">
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

          {/* 問い合わせフォーム / 公式 / SNS */}
          {data.primary_contact_form_url && (
            <div>
              <p className="text-xs font-semibold text-slate-500">問い合わせフォーム</p>
              <a
                href={data.primary_contact_form_url}
                target="_blank"
                rel="noreferrer"
                className="mt-0.5 inline-block break-all text-blue-700 hover:underline"
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
                className="mt-0.5 inline-block break-all text-blue-700 hover:underline"
              >
                {data.official_site_url} ↗
              </a>
            </div>
          )}
          {socials.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">SNS</p>
              <div className="mt-0.5 flex flex-wrap gap-3">
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

          {/* PDF */}
          {pdfs.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">PDFリンク</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                {pdfs.map((p, i) => (
                  <li key={i}>
                    <a
                      href={p.url ?? "#"}
                      target="_blank"
                      rel="noreferrer"
                      className="break-all text-blue-700 hover:underline"
                    >
                      {p.label} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 手動検索クエリ候補 */}
          {data.search_queries && data.search_queries.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">
                手動検索クエリ候補（Google等で検索）
              </p>
              <ul className="mt-1 space-y-1">
                {data.search_queries.map((q, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <code className="rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-800">
                      {q}
                    </code>
                    <CopyButton text={q} />
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* チェックリスト */}
          {data.discovery_checklist && (
            <div>
              <p className="text-xs font-semibold text-slate-500">探索チェックリスト</p>
              <div className="mt-1 flex flex-wrap gap-2">
                {Object.entries(data.discovery_checklist).map(([k, v]) => (
                  <span
                    key={k}
                    className={`rounded px-2 py-0.5 text-xs ${
                      v
                        ? "bg-emerald-100 text-emerald-700"
                        : "bg-slate-100 text-slate-400"
                    }`}
                  >
                    {v ? "✓" : "—"} {CHECKLIST_LABELS[k] ?? k}
                  </span>
                ))}
              </div>
            </div>
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
