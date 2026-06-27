"use client";

import { useEffect, useState } from "react";

import {
  createReplyAssist,
  createReplyGmailDraft,
  fetchEmailProvider,
  fetchReplyAssists,
  formatDateTime,
  INTENT_LABELS,
  REPLY_TONE_LABELS,
  REPLY_TONE_ORDER,
  SENTIMENT_COLORS,
  SENTIMENT_LABELS,
  type EmailProviderInfo,
  type ReplyAssist,
  type ReplyGmailDraftResult,
  type ReplyTone,
} from "@/lib/api";

function List({ items }: { items: string[] | null | undefined }) {
  if (!items || items.length === 0)
    return <span className="text-slate-400">—</span>;
  return (
    <ul className="list-disc space-y-0.5 pl-4">
      {items.map((it, i) => (
        <li key={i}>{it}</li>
      ))}
    </ul>
  );
}

function ResultView({
  row,
  providerLabel,
  onChanged,
}: {
  row: ReplyAssist;
  providerLabel: string;
  onChanged: () => void;
}) {
  const [copied, setCopied] = useState(false);
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<ReplyGmailDraftResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [to, setTo] = useState(row.incoming_from ?? "");

  if (row.status === "failed") {
    return (
      <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        <p className="font-semibold">解析に失敗しました。</p>
        <p className="mt-1 whitespace-pre-wrap break-all text-xs">
          {row.error ?? "原因不明のエラーです。"}
        </p>
      </div>
    );
  }

  async function copyReply() {
    const text = `Subject: ${row.reply_subject ?? ""}\n\n${row.reply_body ?? ""}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  async function makeDraft() {
    if (busy) return;
    setBusy(true);
    setDraft(null);
    setError(null);
    try {
      const r = await createReplyGmailDraft(row.id, to || undefined);
      setDraft(r);
      onChanged();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const isGmail = draft?.provider === "gmail";

  return (
    <div className="mt-3 space-y-4 rounded-lg border border-slate-200 bg-white p-5 text-sm">
      {/* 解析サマリ */}
      <div className="flex flex-wrap items-center gap-2">
        {row.intent && (
          <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
            意図: {INTENT_LABELS[row.intent] ?? row.intent}
          </span>
        )}
        {row.sentiment && (
          <span
            className={`rounded px-2 py-0.5 text-xs font-medium ${
              SENTIMENT_COLORS[row.sentiment] ?? SENTIMENT_COLORS.neutral
            }`}
          >
            温度感: {SENTIMENT_LABELS[row.sentiment] ?? row.sentiment}
          </span>
        )}
        {row.detected_language && (
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600">
            言語: {row.detected_language}
          </span>
        )}
      </div>

      {row.japanese_summary && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs font-semibold text-amber-800">日本語要約</p>
          <p className="mt-1 whitespace-pre-wrap text-xs text-amber-900">
            {row.japanese_summary}
          </p>
        </div>
      )}

      <div>
        <p className="text-xs font-semibold text-slate-500">重要ポイント</p>
        <div className="mt-0.5 text-slate-800">
          <List items={row.key_points} />
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold text-slate-500">相手が求めていること</p>
        <div className="mt-0.5 text-slate-800">
          <List items={row.requested_actions} />
        </div>
      </div>
      <div>
        <p className="text-xs font-semibold text-slate-500">注意点</p>
        <div className="mt-0.5 text-slate-800">
          <List items={row.risks_or_cautions} />
        </div>
      </div>
      {row.recommended_next_action && (
        <div>
          <p className="text-xs font-semibold text-slate-500">推奨次アクション</p>
          <p className="mt-0.5 text-slate-800">{row.recommended_next_action}</p>
        </div>
      )}

      {/* 返信案 */}
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3">
        <div className="flex items-center justify-between">
          <p className="text-xs font-semibold text-slate-500">英語の返信案</p>
          <button
            onClick={copyReply}
            className="rounded border border-slate-300 bg-white px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
          >
            {copied ? "コピーしました" : "コピー"}
          </button>
        </div>
        <p className="mt-2 text-sm font-semibold text-slate-800">
          Subject: {row.reply_subject}
        </p>
        <pre className="mt-1 whitespace-pre-wrap font-sans text-sm text-slate-700">
          {row.reply_body}
        </pre>
      </div>

      {/* Gmail 返信下書き */}
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-1 flex-col text-xs text-slate-500">
          返信先（既定は差出人）
          <input
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
            placeholder="maker@example.com"
            value={to}
            onChange={(e) => setTo(e.target.value)}
          />
        </label>
        <button
          onClick={makeDraft}
          disabled={busy}
          className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {busy ? "作成中…" : `${providerLabel}に返信下書き作成`}
        </button>
      </div>

      {draft && (
        <div className="rounded-md border border-green-200 bg-green-50 p-3 text-xs text-green-800">
          <p className="font-semibold">
            {isGmail
              ? "Gmailの返信下書きを作成しました。"
              : "モック返信下書きを作成しました。Gmailには保存されず、この画面で確認できます。"}
          </p>
          <p className="mt-1 text-green-700">宛先: {draft.to}</p>
          {isGmail && draft.web_link && (
            <a
              href={draft.web_link}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block font-medium text-blue-700 hover:underline"
            >
              Gmailで開く ↗
            </a>
          )}
        </div>
      )}
      {row.gmail_draft_id && !draft && (
        <p className="text-xs text-slate-400">
          返信下書き作成済み（id: {row.gmail_draft_id}）
          {row.gmail_web_link && (
            <>
              {" ・ "}
              <a
                href={row.gmail_web_link}
                target="_blank"
                rel="noreferrer"
                className="text-blue-700 hover:underline"
              >
                Gmailで開く ↗
              </a>
            </>
          )}
        </p>
      )}
      {error && (
        <div className="rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          <p className="font-semibold">返信下書きの作成に失敗しました。</p>
          <p className="mt-1 break-all">{error}</p>
        </div>
      )}

      <p className="text-right text-xs text-slate-400">
        {row.model} ・ {formatDateTime(row.updated_at)}
      </p>
    </div>
  );
}

export default function ReplyAssistPanel({ projectId }: { projectId: number }) {
  const [subject, setSubject] = useState("");
  const [from, setFrom] = useState("");
  const [body, setBody] = useState("");
  const [tone, setTone] = useState<ReplyTone>("professional");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [latest, setLatest] = useState<ReplyAssist | null>(null);
  const [provider, setProvider] = useState<EmailProviderInfo | null>(null);

  function reload() {
    fetchReplyAssists(projectId)
      .then((list) => setLatest(list[0] ?? null))
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    reload();
    fetchEmailProvider()
      .then(setProvider)
      .catch(() => setProvider(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function onAnalyze() {
    if (!body.trim()) {
      setError("返信本文を貼り付けてください。");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const r = await createReplyAssist(projectId, {
        incoming_subject: subject || undefined,
        incoming_from: from || undefined,
        incoming_body: body,
        reply_tone: tone,
      });
      setLatest(r);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const providerLabel =
    provider?.provider === "gmail" ? "Gmail" : "メール（モック）";

  return (
    <div className="mt-8">
      <h2 className="text-sm font-semibold text-slate-700">返信メールAIサポート</h2>
      <p className="mt-1 text-xs text-slate-400">
        メーカーからの返信メールを貼り付けると、AIが要約・意図分析と英語の返信案を作成します（送信はしません）。
      </p>

      <div className="mt-3 space-y-2 rounded-lg border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap gap-2">
          <label className="flex flex-1 flex-col text-xs text-slate-500">
            返信メール件名
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="Re: ..."
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
            />
          </label>
          <label className="flex flex-1 flex-col text-xs text-slate-500">
            差出人メール
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="maker@example.com"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
            />
          </label>
        </div>
        <label className="flex flex-col text-xs text-slate-500">
          返信本文を貼り付け
          <textarea
            className="mt-1 h-32 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
            placeholder="Paste the maker's reply here..."
            value={body}
            onChange={(e) => setBody(e.target.value)}
          />
        </label>
        <div className="flex flex-wrap items-end justify-between gap-2">
          <label className="flex flex-col text-xs text-slate-500">
            返信トーン
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={tone}
              onChange={(e) => setTone(e.target.value as ReplyTone)}
            >
              {REPLY_TONE_ORDER.map((t) => (
                <option key={t} value={t}>
                  {REPLY_TONE_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={onAnalyze}
            disabled={busy}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "解析中…" : "AIで返信案を作成"}
          </button>
        </div>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {latest && (
        <ResultView
          row={latest}
          providerLabel={providerLabel}
          onChanged={reload}
        />
      )}
    </div>
  );
}
