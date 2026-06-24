"use client";

import { useEffect, useState } from "react";

import {
  EMAIL_TYPE_LABELS,
  EMAIL_TYPE_ORDER,
  fetchEmailDrafts,
  formatDateTime,
  generateEmailDrafts,
  type EmailDraft,
  type EmailType,
} from "@/lib/api";

function DraftCard({ draft }: { draft: EmailDraft }) {
  const [copied, setCopied] = useState(false);

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

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
          {EMAIL_TYPE_LABELS[draft.email_type]}
        </span>
        <button
          onClick={copy}
          className="rounded border border-slate-300 px-2 py-1 text-xs text-slate-600 hover:bg-slate-50"
        >
          {copied ? "コピーしました" : "コピー"}
        </button>
      </div>
      <p className="mt-2 text-sm font-semibold text-slate-800">
        Subject: {draft.subject}
      </p>
      <pre className="mt-2 whitespace-pre-wrap font-sans text-sm text-slate-700">
        {draft.body}
      </pre>
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

  useEffect(() => {
    fetchEmailDrafts(projectId)
      .then(setDrafts)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      await generateEmailDrafts(projectId);
      setDrafts(await fetchEmailDrafts(projectId));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  // 種別ごとに最新（list は新しい順なので最初に出現したものが最新）
  const latestByType: Partial<Record<EmailType, EmailDraft>> = {};
  for (const d of drafts) {
    if (!latestByType[d.email_type]) latestByType[d.email_type] = d;
  }
  const hasAny = drafts.length > 0;

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
        ※ 自動送信はしません。内容を確認・編集のうえご利用ください。
      </p>

      <div className="mt-3 space-y-3">
        {!hasAny && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            まだ下書きがありません。「下書きを生成」で初回営業・独占販売権打診・フォローアップの3種を作成します。
          </p>
        )}
        {hasAny &&
          EMAIL_TYPE_ORDER.map((t) => {
            const d = latestByType[t];
            return d ? <DraftCard key={t} draft={d} /> : null;
          })}
      </div>
    </div>
  );
}
