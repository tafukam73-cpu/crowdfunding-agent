"use client";

import { useEffect, useState } from "react";

import {
  createProviderDraft,
  EMAIL_TYPE_LABELS,
  EMAIL_TYPE_ORDER,
  fetchEmailDrafts,
  fetchEmailProvider,
  formatDateTime,
  generateEmailDrafts,
  type EmailDraft,
  type EmailProviderInfo,
  type EmailType,
} from "@/lib/api";

function DraftCard({
  draft,
  to,
  providerLabel,
  onCreated,
}: {
  draft: EmailDraft;
  to: string;
  providerLabel: string;
  onCreated: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [link, setLink] = useState<string | null>(null);

  async function copy() {
    const text = `Subject: ${draft.subject}\n\n${draft.body}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  async function makeDraft() {
    setBusy(true);
    setMsg(null);
    setLink(null);
    try {
      const r = await createProviderDraft(draft.id, to || undefined);
      setMsg(`${r.provider} に下書きを作成しました（宛先: ${r.to}）`);
      setLink(r.web_link);
      onCreated();
    } catch (e) {
      setMsg(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
          {EMAIL_TYPE_LABELS[draft.email_type]}
        </span>
        <div className="flex items-center gap-2">
          <button
            onClick={makeDraft}
            disabled={busy}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            {busy ? "作成中…" : `${providerLabel}に下書き作成`}
          </button>
          <button
            onClick={copy}
            className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
          >
            {copied ? "コピーしました" : "コピー"}
          </button>
        </div>
      </div>
      <p className="mt-2 text-sm font-semibold text-slate-800">
        Subject: {draft.subject}
      </p>
      <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-slate-700">
        {draft.body}
      </pre>

      {msg && (
        <p className="mt-2 text-xs text-slate-600">
          {msg}
          {link && (
            <a
              href={link}
              target="_blank"
              rel="noreferrer"
              className="ml-2 text-blue-700 hover:underline"
            >
              開く ↗
            </a>
          )}
        </p>
      )}
      {draft.provider_draft_id && !msg && (
        <p className="mt-2 text-xs text-slate-400">
          {draft.provider} 下書き作成済み（id: {draft.provider_draft_id}）
        </p>
      )}

      <p className="mt-2 text-right text-xs text-slate-400">
        {draft.model} ・ {formatDateTime(draft.created_at)}
      </p>
    </div>
  );
}

export default function EmailDraftPanel({ projectId }: { projectId: number }) {
  const [drafts, setDrafts] = useState<EmailDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState<EmailProviderInfo | null>(null);
  const [to, setTo] = useState("");

  function reload() {
    fetchEmailDrafts(projectId)
      .then(setDrafts)
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    reload();
    fetchEmailProvider()
      .then(setProvider)
      .catch(() => setProvider(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      await generateEmailDrafts(projectId);
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const latestByType: Partial<Record<EmailType, EmailDraft>> = {};
  for (const d of drafts) {
    if (!latestByType[d.email_type]) latestByType[d.email_type] = d;
  }
  const hasAny = drafts.length > 0;
  const providerLabel =
    provider?.provider === "gmail" ? "Gmail" : "メール（モック）";

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">営業メール下書き</h2>
        <button
          onClick={onGenerate}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "生成中…" : hasAny ? "再生成" : "下書きを生成"}
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <p className="mt-1 text-xs text-slate-400">
        ※ 自動送信はしません。「{providerLabel}に下書き作成」で下書きを作り、送信は
        {provider?.provider === "gmail" ? "Gmail 上で" : "メールサービス上で"}最終確認のうえ行ってください。
      </p>

      <div className="mt-2 flex items-end gap-2">
        <label className="flex flex-1 flex-col text-xs text-slate-500">
          宛先（空欄ならメーカー担当者/連絡先から自動設定）
          <input
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
            placeholder="maker@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        {provider && (
          <span className="pb-1 text-xs text-slate-400">
            下書き先: {providerLabel}
          </span>
        )}
      </div>

      <div className="mt-3 space-y-3">
        {!hasAny && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            まだ下書きがありません。「下書きを生成」で初回営業・独占販売権打診・フォローアップの3種を作成します。
          </p>
        )}
        {hasAny &&
          EMAIL_TYPE_ORDER.map((t) => {
            const d = latestByType[t];
            return d ? (
              <DraftCard
                key={t}
                draft={d}
                to={to}
                providerLabel={providerLabel}
                onCreated={reload}
              />
            ) : null;
          })}
      </div>
    </div>
  );
}
