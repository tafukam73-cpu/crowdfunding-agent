"use client";

import { useEffect, useState } from "react";

import {
  createProviderDraft,
  EMAIL_TONE_LABELS,
  EMAIL_TONE_ORDER,
  EMAIL_TYPE_LABELS,
  EMAIL_TYPE_ORDER,
  fetchCompanyResearch,
  fetchContactDiscovery,
  fetchEmailDrafts,
  fetchEmailProvider,
  formatDateTime,
  generateEmailDrafts,
  selectEmailSubject,
  type EmailDraft,
  type EmailProviderInfo,
  type EmailTone,
  type EmailType,
  type ProviderDraftResult,
} from "@/lib/api";

// 個別化ポイントの表示（送信前に「なぜ個別化されているか」を確認できる）
function PersonalizationBox({ draft }: { draft: EmailDraft }) {
  const ctx = draft.personalization_context;
  const productName = ctx?.product_name;
  const highlights =
    draft.product_highlights ?? ctx?.product_highlights ?? [];
  const compliment = draft.personalized_compliment ?? ctx?.personalized_compliment;
  const japanAngle = ctx?.japan_market_angle;

  // 個別化情報が一切無い旧データでは何も表示しない（画面を壊さない）
  if (!productName && highlights.length === 0 && !compliment && !japanAngle) {
    return null;
  }

  return (
    <div className="mt-3 rounded-md border border-sky-200 bg-sky-50 p-3">
      <p className="text-xs font-semibold text-sky-800">
        個別化ポイント（送信前チェック：なぜこのメールが個別化されているか）
      </p>
      <dl className="mt-1 space-y-1 text-xs text-sky-900">
        {productName && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-sky-700">商品名</dt>
            <dd>{productName}</dd>
          </div>
        )}
        {highlights.length > 0 && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-sky-700">注目ポイント</dt>
            <dd>
              <ul className="list-disc pl-4">
                {highlights.map((h, i) => (
                  <li key={i}>{h}</li>
                ))}
              </ul>
            </dd>
          </div>
        )}
        {compliment && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-sky-700">称賛ポイント</dt>
            <dd>{compliment}</dd>
          </div>
        )}
        {japanAngle && (
          <div className="flex gap-2">
            <dt className="shrink-0 font-medium text-sky-700">日本市場での訴求</dt>
            <dd>{japanAngle}</dd>
          </div>
        )}
      </dl>
    </div>
  );
}

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
  const [result, setResult] = useState<ProviderDraftResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 件名候補（無い古い下書きは subject 単体にフォールバック）
  const options =
    draft.subject_options && draft.subject_options.length > 0
      ? draft.subject_options
      : [draft.subject];
  // 選択中の件名（保存済みの subject / selected_subject を初期値に）
  const [subject, setSubject] = useState(
    draft.selected_subject || draft.subject
  );
  const [savingSubject, setSavingSubject] = useState(false);

  async function chooseSubject(s: string) {
    if (s === subject) return;
    setSubject(s); // 楽観的更新
    setSavingSubject(true);
    try {
      await selectEmailSubject(draft.id, s);
      onCreated(); // 一覧を再読込して保存内容を反映
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSavingSubject(false);
    }
  }

  async function copy() {
    const text = `Subject: ${subject}\n\n${draft.body}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopied(false);
    }
  }

  async function makeDraft() {
    if (busy) return; // 二重クリック防止
    setBusy(true);
    setResult(null);
    setError(null);
    try {
      // 選択中の件名を確実に反映してから下書き作成
      await selectEmailSubject(draft.id, subject);
      const r = await createProviderDraft(draft.id, to || undefined);
      setResult(r);
      onCreated();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  const isGmail = result?.provider === "gmail";

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
            {EMAIL_TYPE_LABELS[draft.email_type]}
          </span>
          {draft.tone && (
            <span className="rounded bg-indigo-50 px-2 py-0.5 text-xs font-medium text-indigo-700">
              {EMAIL_TONE_LABELS[draft.tone as EmailTone] ?? draft.tone}
            </span>
          )}
        </div>
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

      {/* 件名 3 案（選択可能） */}
      <div className="mt-3">
        <p className="text-xs font-medium text-slate-500">
          件名（3案から選択）{savingSubject && " ・保存中…"}
        </p>
        <div className="mt-1 space-y-1">
          {options.map((opt, i) => (
            <label
              key={i}
              className={`flex cursor-pointer items-start gap-2 rounded border px-2 py-1.5 text-sm ${
                opt === subject
                  ? "border-blue-300 bg-blue-50 text-slate-900"
                  : "border-slate-200 text-slate-700 hover:bg-slate-50"
              }`}
            >
              <input
                type="radio"
                name={`subject-${draft.id}`}
                className="mt-0.5"
                checked={opt === subject}
                onChange={() => chooseSubject(opt)}
              />
              <span>{opt}</span>
            </label>
          ))}
        </div>
      </div>

      {/* 個別化ポイント（なぜこのメールが個別化されているか） */}
      <PersonalizationBox draft={draft} />

      {/* 英文本文 */}
      <pre className="mt-3 whitespace-pre-wrap font-sans text-sm text-slate-700">
        {draft.body}
      </pre>

      {/* 日本語要約（送信前確認用） */}
      {draft.japanese_summary && (
        <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 p-3">
          <p className="text-xs font-semibold text-amber-800">
            日本語要約（送信前チェック）
          </p>
          <pre className="mt-1 whitespace-pre-wrap font-sans text-xs text-amber-900">
            {draft.japanese_summary}
          </pre>
        </div>
      )}

      {result && (
        <div className="mt-3 rounded-md border border-green-200 bg-green-50 p-3 text-xs text-green-800">
          <p className="font-semibold">
            {isGmail
              ? "Gmailの下書きを作成しました。"
              : "モック下書きを作成しました。Gmailには保存されず、この画面上で確認できます。"}
          </p>
          <p className="mt-1 text-green-700">宛先: {result.to}</p>
          <p className="text-green-700">件名: {subject}</p>
          {isGmail && result.web_link && (
            <a
              href={result.web_link}
              target="_blank"
              rel="noreferrer"
              className="mt-1 inline-block font-medium text-blue-700 hover:underline"
            >
              Gmailで開く ↗
            </a>
          )}
          {!isGmail && (
            <button
              onClick={copy}
              className="mt-2 rounded border border-green-300 bg-white px-2 py-1 font-medium text-green-700 hover:bg-green-100"
            >
              {copied ? "コピーしました" : "Subject / Body をコピー"}
            </button>
          )}
        </div>
      )}
      {error && (
        <div className="mt-3 rounded-md border border-red-200 bg-red-50 p-3 text-xs text-red-700">
          <p className="font-semibold">処理に失敗しました。</p>
          <p className="mt-1 break-all">{error}</p>
        </div>
      )}
      {draft.provider_draft_id && !result && !error && (
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

export default function EmailDraftPanel({
  projectId,
  researchVersion = 0,
  discoveryVersion = 0,
}: {
  projectId: number;
  researchVersion?: number;
  discoveryVersion?: number;
}) {
  const [drafts, setDrafts] = useState<EmailDraft[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [provider, setProvider] = useState<EmailProviderInfo | null>(null);
  const [to, setTo] = useState("");
  const [tone, setTone] = useState<EmailTone>("professional");
  // 企業リサーチが反映可能か（completed が存在するか）
  const [researchApplied, setResearchApplied] = useState(false);
  // 連絡先探索で見つかった宛先候補
  const [primaryEmail, setPrimaryEmail] = useState<string | null>(null);
  // ユーザーが宛先を手で編集したら自動プリフィルしない
  const [toEdited, setToEdited] = useState(false);

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

  // 企業リサーチの有無（completed）を確認してバッジ表示に反映
  useEffect(() => {
    fetchCompanyResearch(projectId)
      .then((r) => setResearchApplied(r?.research_status === "completed"))
      .catch(() => setResearchApplied(false));
  }, [projectId, researchVersion]);

  // 連絡先探索の primary_email を宛先候補として取得し、未編集なら自動入力
  useEffect(() => {
    fetchContactDiscovery(projectId)
      .then((d) => {
        const email = d?.primary_email ?? null;
        setPrimaryEmail(email);
        if (email && !toEdited) setTo(email);
      })
      .catch(() => setPrimaryEmail(null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, discoveryVersion]);

  async function onGenerate() {
    setBusy(true);
    setError(null);
    try {
      await generateEmailDrafts(projectId, tone);
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  // 種別ごとに最新の下書き（reload で先頭が最新）。key に id を含めて
  // 再生成時にカードの内部 state（選択件名など）がリセットされるようにする。
  const latestByType: Partial<Record<EmailType, EmailDraft>> = {};
  for (const d of drafts) {
    if (!latestByType[d.email_type]) latestByType[d.email_type] = d;
  }
  const hasAny = drafts.length > 0;
  const providerLabel =
    provider?.provider === "gmail" ? "Gmail" : "メール（モック）";

  return (
    <div className="mt-8">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <h2 className="text-sm font-semibold text-slate-700">営業メール下書き</h2>
          {researchApplied && (
            <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
              企業リサーチ反映済み
            </span>
          )}
        </div>
        <div className="flex items-end gap-2">
          <label className="flex flex-col text-xs text-slate-500">
            トーン
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={tone}
              onChange={(e) => setTone(e.target.value as EmailTone)}
            >
              {EMAIL_TONE_ORDER.map((t) => (
                <option key={t} value={t}>
                  {EMAIL_TONE_LABELS[t]}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={onGenerate}
            disabled={busy}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {busy ? "生成中…" : hasAny ? "再生成" : "下書きを生成"}
          </button>
        </div>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <p className="mt-1 text-xs text-slate-400">
        ※ 自動送信はしません。トーンを選び「下書きを生成」すると、件名3案・本文・
        日本語要約を作成します。「{providerLabel}に下書き作成」で下書きを作り、送信は
        {provider?.provider === "gmail" ? "Gmail 上で" : "メールサービス上で"}最終確認のうえ行ってください。
      </p>

      <div className="mt-2 flex items-end gap-2">
        <label className="flex flex-1 flex-col text-xs text-slate-500">
          宛先（空欄ならメーカー担当者/連絡先から自動設定）
          <input
            className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
            placeholder="maker@example.com"
            value={to}
            onChange={(e) => {
              setToEdited(true);
              setTo(e.target.value);
            }}
          />
        </label>
        {provider && (
          <span className="pb-1 text-xs text-slate-400">
            下書き先: {providerLabel}
          </span>
        )}
      </div>
      {primaryEmail && (
        <p className="mt-1 text-xs text-slate-500">
          連絡先探索の宛先候補:{" "}
          <button
            onClick={() => {
              setToEdited(true);
              setTo(primaryEmail);
            }}
            className="font-medium text-blue-700 hover:underline"
          >
            {primaryEmail}
          </button>{" "}
          {to === primaryEmail ? "（適用中・編集可）" : "（クリックで宛先に設定）"}
        </p>
      )}

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
                key={`${t}-${d.id}`}
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
