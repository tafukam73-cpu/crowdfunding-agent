"use client";

import { useEffect, useState } from "react";

import {
  type AiCandidateEmail,
  applyDiscoveryToCrm,
  type ContactDiscovery,
  fetchContactDiscovery,
  fetchOutreachMessage,
  formatDateTime,
  type OutreachMessage,
  runAiContactResearch,
  runContactDiscovery,
} from "@/lib/api";

// EmailDraftPanel と共有する「Gmail 下書きの宛先候補」セッションキー。
// AI 候補メールを「Gmail宛先に使用」したとき、メール作成画面の宛先に引き継ぐ。
export function gmailToKey(projectId: number): string {
  return `cf:gmailTo:${projectId}`;
}

// 短文アウトリーチ文を出す対象チャネル（メールアドレスが無い場合の代替手段）
const SHORT_OUTREACH_CHANNELS = [
  "contact_form",
  "instagram",
  "linkedin",
  "facebook",
];

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

// 案件詳細から外部連絡先へ一発で飛べるクイックボタン用の定義。
// channel は recommended_channel の値と突き合わせて「おすすめ」表示に使う。
// searchUrl がある項目は URL 未取得でもブランド名/案件名での検索ボタンを表示する。
const QUICK_LINKS: {
  key: keyof ContactDiscovery;
  label: string;
  channel: string;
  searchUrl?: (keyword: string) => string;
}[] = [
  {
    key: "primary_contact_form_url",
    label: "問い合わせフォーム",
    channel: "contact_form",
    searchUrl: (kw) =>
      `https://www.google.com/search?q=${encodeURIComponent(`${kw} contact form`)}`,
  },
  {
    key: "instagram_url",
    label: "Instagram",
    channel: "instagram",
    searchUrl: (kw) =>
      `https://www.instagram.com/explore/search/keyword/?q=${encodeURIComponent(kw)}`,
  },
  {
    key: "linkedin_url",
    label: "LinkedIn",
    channel: "linkedin",
    searchUrl: (kw) =>
      `https://www.linkedin.com/search/results/companies/?keywords=${encodeURIComponent(kw)}`,
  },
  {
    key: "facebook_url",
    label: "Facebook",
    channel: "facebook",
    searchUrl: (kw) =>
      `https://www.facebook.com/search/top?q=${encodeURIComponent(kw)}`,
  },
  { key: "twitter_url", label: "X / Twitter", channel: "twitter" },
  { key: "youtube_url", label: "YouTube", channel: "youtube" },
  { key: "official_site_url", label: "公式サイト", channel: "official_site" },
];

// 発見済みの外部連絡先リンクへ新しいタブで飛べるクイックボタン群。
// URL が存在するチャネルは「開く」、未取得でも searchUrl を持つチャネルは
// ブランド名/案件名（searchKeyword）での「検索」ボタンを表示する。
function QuickContactLinks({
  data,
  searchKeyword,
}: {
  data: ContactDiscovery;
  searchKeyword: string;
}) {
  const keyword = searchKeyword.trim();

  // 各リンクを open（URLあり）/ search（URLなし＋検索可）に振り分ける。
  const items = QUICK_LINKS.map((l) => {
    const url = data[l.key];
    if (typeof url === "string" && url.length > 0) {
      return { link: l, href: url, mode: "open" as const };
    }
    if (l.searchUrl && keyword) {
      return { link: l, href: l.searchUrl(keyword), mode: "search" as const };
    }
    return null;
  }).filter((x): x is NonNullable<typeof x> => x !== null);

  if (items.length === 0) {
    return (
      <div>
        <p className="text-xs font-semibold text-slate-500">外部連絡先リンク</p>
        <p className="mt-1 text-xs text-slate-400">
          利用可能な外部連絡先リンクはまだ見つかっていません。
        </p>
      </div>
    );
  }

  return (
    <div>
      <p className="text-xs font-semibold text-slate-500">
        外部連絡先リンク（クリックで新しいタブ）
      </p>
      <div className="mt-1.5 flex flex-wrap gap-2">
        {items.map(({ link, href, mode }) => {
          const recommended =
            mode === "open" && data.recommended_channel === link.channel;
          const label =
            mode === "search"
              ? link.channel === "contact_form"
                ? "Googleで問い合わせフォーム検索"
                : `${link.label}で検索`
              : recommended
                ? `★ おすすめ：${link.label}`
                : `${link.label}を開く`;
          const className = recommended
            ? "inline-flex items-center gap-1 rounded-md border border-emerald-400 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm hover:bg-emerald-100"
            : mode === "search"
              ? "inline-flex items-center gap-1 rounded-md border border-dashed border-sky-400 bg-sky-50 px-3 py-1.5 text-xs font-medium text-sky-700 hover:bg-sky-100"
              : "inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50";
          return (
            <a
              key={link.key as string}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className={className}
            >
              {label}
              <span aria-hidden>{mode === "search" ? "🔍" : "↗"}</span>
            </a>
          );
        })}
      </div>
    </div>
  );
}

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

// 短文アウトリーチ文を生成できるチャネル（手動選択用）。
const OUTREACH_CHANNEL_OPTIONS: { value: string; label: string }[] = [
  { value: "contact_form", label: "問い合わせフォーム" },
  { value: "instagram", label: "Instagram" },
  { value: "linkedin", label: "LinkedIn" },
  { value: "facebook", label: "Facebook" },
];

// メールアドレスが見つからない案件向けの短文アウトリーチ文（フォーム / SNS DM 用）。
// 推奨チャネルに依存せず常に表示し、ユーザーがチャネルを選んで生成できる。
// URL が無くても本文は案件情報から作れるため、チャネル選択だけで利用可能。
function ShortOutreach({
  projectId,
  defaultChannel,
}: {
  projectId: number;
  defaultChannel: string;
}) {
  const [channel, setChannel] = useState(defaultChannel);
  const [msg, setMsg] = useState<OutreachMessage | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      setMsg(await fetchOutreachMessage(projectId, channel));
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="rounded-md border border-violet-200 bg-violet-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-violet-800">
          短文アウトリーチ文（メール不要・フォーム / SNS DM 用）
        </p>
        <div className="flex flex-wrap items-center gap-2">
          <label className="flex items-center gap-1 text-xs text-violet-700">
            チャネル
            <select
              value={channel}
              onChange={(e) => {
                setChannel(e.target.value);
                setMsg(null);
              }}
              className="rounded border border-violet-300 bg-white px-2 py-1 text-xs text-violet-800"
            >
              {OUTREACH_CHANNEL_OPTIONS.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
          </label>
          <button
            onClick={generate}
            disabled={busy}
            className="rounded border border-violet-300 bg-white px-2 py-1 text-xs font-medium text-violet-700 hover:bg-violet-100 disabled:opacity-50"
          >
            {busy ? "生成中…" : msg ? "再生成" : "短文を作成"}
          </button>
        </div>
      </div>
      <p className="mt-1 text-xs text-violet-600">
        メールアドレスが無くても、選んだチャネル（問い合わせフォーム / Instagram /
        LinkedIn / Facebook）のDMにそのまま貼り付けられる短い営業文（約300〜600文字）を
        作成します。送信は手動で行ってください。
      </p>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {msg && (
        <div className="mt-2 rounded border border-violet-200 bg-white p-3">
          <pre className="whitespace-pre-wrap font-sans text-sm text-slate-800">
            {msg.text}
          </pre>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CopyButton text={msg.text} label="アウトリーチ文をコピー" />
            <span className="text-xs text-slate-400">
              {msg.channel_label} ・ {msg.char_count}文字
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

const CONFIDENCE_LABELS: Record<string, string> = {
  high: "高",
  medium: "中",
  low: "低",
};

// AI 候補メールのカード（コピー / CRM反映 / Gmail宛先に使用）。
function AiEmailRow({
  cand,
  onApply,
  onUseAsGmailTo,
}: {
  cand: AiCandidateEmail;
  onApply: (email: string) => void;
  onUseAsGmailTo: (email: string) => void;
}) {
  return (
    <li className="rounded border border-fuchsia-200 bg-white p-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="rounded bg-fuchsia-100 px-2 py-0.5 text-xs font-medium text-fuchsia-700">
          {cand.confidence
            ? `信頼度 ${CONFIDENCE_LABELS[cand.confidence] ?? cand.confidence}`
            : "信頼度 —"}
          {typeof cand.score === "number" ? ` ・ ${cand.score}` : ""}
        </span>
        <span className="font-medium text-slate-800">{cand.email}</span>
        <CopyButton text={cand.email} label="コピー" />
        <button
          onClick={() => onApply(cand.email)}
          className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
        >
          CRMに反映
        </button>
        <button
          onClick={() => onUseAsGmailTo(cand.email)}
          className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
        >
          Gmail宛先に使用
        </button>
      </div>
      {cand.reason && (
        <p className="mt-1 text-xs text-slate-500">根拠: {cand.reason}</p>
      )}
      {cand.source_url && (
        <a
          href={cand.source_url}
          target="_blank"
          rel="noreferrer"
          className="mt-0.5 inline-block break-all text-xs text-blue-700 hover:underline"
        >
          出典: {cand.source_url} ↗
        </a>
      )}
    </li>
  );
}

// AI 連絡先リサーチ（自動抽出の補完。出典付き候補・推奨チャネル・検索クエリ）。
function AiResearchSection({
  projectId,
  data,
  busy,
  error,
  onRun,
  onApply,
}: {
  projectId: number;
  data: ContactDiscovery | null;
  busy: boolean;
  error: string | null;
  onRun: () => void;
  onApply: (email: string) => void;
}) {
  const [gmailMsg, setGmailMsg] = useState<string | null>(null);

  function onUseAsGmailTo(email: string) {
    try {
      sessionStorage.setItem(gmailToKey(projectId), email);
    } catch {
      /* sessionStorage 不可環境では無視 */
    }
    setGmailMsg(
      `「${email}」をメール作成画面（STEP 3）の宛先候補に設定しました。`
    );
  }

  const researched = data?.ai_researched;
  const candidates = data?.ai_candidate_emails ?? [];
  const aiSocials: { label: string; url: string | null | undefined }[] = [
    { label: "Instagram", url: data?.ai_instagram_url },
    { label: "Facebook", url: data?.ai_facebook_url },
    { label: "LinkedIn", url: data?.ai_linkedin_url },
  ];

  return (
    <div className="rounded-md border border-fuchsia-200 bg-fuchsia-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-fuchsia-900">
            🤖 AI連絡先リサーチ
          </p>
          <p className="mt-0.5 text-xs text-fuchsia-700">
            自動抽出でメールが見つからない場合に、AIが公式サイト・SNS・検索クエリから
            営業に使える連絡先を整理します（推測メールは作りません）。
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-fuchsia-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-fuchsia-600 disabled:opacity-50"
        >
          {busy ? "AI調査中…" : researched ? "AIで再調査" : "AIで連絡先を調査"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {!researched && !error && (
        <p className="mt-3 text-xs text-fuchsia-700">
          まだAI調査は実行されていません。上のボタンで実行できます（Claude未設定時は
          モックで動作）。
        </p>
      )}

      {researched && data && (
        <div className="mt-3 space-y-3 text-sm">
          {/* 確度 & 推奨チャネル */}
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded bg-fuchsia-100 px-2 py-0.5 text-xs font-medium text-fuchsia-800">
              AI確度: {data.ai_confidence_score ?? 0} / 100
            </span>
            {data.ai_recommended_channel && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                AI推奨チャネル:{" "}
                {CHANNEL_LABELS[data.ai_recommended_channel] ??
                  data.ai_recommended_channel}
              </span>
            )}
            {data.ai_model && (
              <span className="text-xs text-slate-400">{data.ai_model}</span>
            )}
          </div>

          {/* 注意メモ */}
          {data.ai_notes && (
            <div className="rounded-md border border-amber-200 bg-amber-50 p-2 text-xs text-amber-900">
              {data.ai_notes}
            </div>
          )}

          {/* AI primary email */}
          {data.ai_primary_email ? (
            <div>
              <p className="text-xs font-semibold text-fuchsia-700">
                AI主要メール（出典付き・再検証済み）
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-900">
                  {data.ai_primary_email}
                </span>
                <CopyButton text={data.ai_primary_email} label="コピー" />
                <button
                  onClick={() => onApply(data.ai_primary_email as string)}
                  className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                >
                  CRMに反映
                </button>
                <button
                  onClick={() => onUseAsGmailTo(data.ai_primary_email as string)}
                  className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                >
                  Gmail宛先に使用
                </button>
              </div>
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              AIは出典付きの確実なメールを発見できませんでした。下記の推奨チャネル・
              検索クエリで営業先を確保してください（推測メールは候補にしていません）。
            </p>
          )}

          {gmailMsg && (
            <p className="text-xs font-medium text-blue-700">{gmailMsg}</p>
          )}

          {/* 候補メール一覧 */}
          {candidates.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-fuchsia-700">
                AI候補メール（出典付き・優先度順）
              </p>
              <ul className="mt-1 space-y-1.5">
                {candidates.map((c) => (
                  <AiEmailRow
                    key={c.email}
                    cand={c}
                    onApply={onApply}
                    onUseAsGmailTo={onUseAsGmailTo}
                  />
                ))}
              </ul>
            </div>
          )}

          {/* AIが見つけたフォーム / SNS */}
          {(data.ai_contact_form_url ||
            aiSocials.some((s) => s.url)) && (
            <div className="flex flex-wrap gap-3">
              {data.ai_contact_form_url && (
                <a
                  href={data.ai_contact_form_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  問い合わせフォーム ↗
                </a>
              )}
              {aiSocials
                .filter((s) => s.url)
                .map((s) => (
                  <a
                    key={s.label}
                    href={s.url as string}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-blue-700 hover:underline"
                  >
                    {s.label} ↗
                  </a>
                ))}
            </div>
          )}

          {/* AI検索クエリ */}
          {data.ai_search_queries && data.ai_search_queries.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-fuchsia-700">
                AI検索クエリ（手動リサーチ用）
              </p>
              <ul className="mt-1 space-y-1">
                {data.ai_search_queries.map((q, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <code className="break-all rounded bg-white px-1.5 py-0.5 text-xs text-slate-800">
                      {q}
                    </code>
                    <CopyButton text={q} />
                    <a
                      href={`https://www.google.com/search?q=${encodeURIComponent(q)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      Googleで開く ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 出典 URL */}
          {data.ai_sources && data.ai_sources.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                AIが参照した出典（{data.ai_sources.length}）
              </summary>
              <ul className="mt-1 space-y-0.5">
                {data.ai_sources.map((s, i) => (
                  <li key={i} className="break-all">
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 hover:underline"
                    >
                      {s.note ? `${s.note}: ` : ""}
                      {s.url} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  );
}

export default function ContactDiscoveryPanel({
  projectId,
  searchKeyword,
  onChanged,
}: {
  projectId: number;
  // SNS / 検索フォールバック用のキーワード（メーカー名 → 無ければ案件名）。
  searchKeyword: string;
  onChanged?: () => void;
}) {
  const [data, setData] = useState<ContactDiscovery | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [applyMsg, setApplyMsg] = useState<string | null>(null);
  const [aiBusy, setAiBusy] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);

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

  async function onRunAi() {
    setAiBusy(true);
    setAiError(null);
    setApplyMsg(null);
    try {
      // AI リサーチは最新の探索結果（ai_* 含む）を返す。土台が無ければサーバ側で
      // 自動探索を先に実行する。
      setData(await runAiContactResearch(projectId));
      onChanged?.();
    } catch (e) {
      setAiError(String(e));
    } finally {
      setAiBusy(false);
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
  // 運営会社（プラットフォーム）のメールは営業先ではないため UI に表示しない
  const discoveredEmails = (data?.discovered_emails ?? []).filter(
    (e) => e.email_owner !== "platform"
  );

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

      {/* AI連絡先リサーチ（自動抽出とは区別して表示。data が無くても実行可能） */}
      <div className="mt-3">
        <AiResearchSection
          projectId={projectId}
          data={data}
          busy={aiBusy}
          error={aiError}
          onRun={onRunAi}
          onApply={onApply}
        />
      </div>

      {!data && !error && (
        <p className="mt-3 rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
          まだ自動探索されていません。「連絡先を探索」を押すと、営業可能な連絡手段を総合評価します（上のAI連絡先リサーチは単独でも実行できます）。
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
            <span className="rounded bg-slate-200 px-2 py-0.5 text-xs font-semibold text-slate-700">
              自動抽出
            </span>
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

          {/* 短文アウトリーチ文（常に表示。チャネルを選んでメール不要で生成） */}
          <ShortOutreach
            projectId={projectId}
            defaultChannel={
              data.recommended_channel &&
              SHORT_OUTREACH_CHANNELS.includes(data.recommended_channel)
                ? data.recommended_channel
                : "contact_form"
            }
          />

          {/* 外部連絡先へのクイックリンク（短文を生成→コピー→そのまま開いて貼り付け）。
              URL が無いSNS/フォームはブランド名・案件名での検索ボタンを表示する。 */}
          <QuickContactLinks data={data} searchKeyword={searchKeyword} />

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

          {/* 発見メール（運営会社=platform のメールは非表示） */}
          {discoveredEmails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-slate-500">
                発見メール（優先度順）
              </p>
              <ul className="mt-1 space-y-1">
                {discoveredEmails.map((e) => (
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

          {/* Google検索アシスト（メールが無い企業でも手動で営業先を探せる検索候補） */}
          <div>
            <p className="text-xs font-semibold text-slate-500">
              Google検索アシスト（手動で営業先を探す）
            </p>
            {data.search_queries && data.search_queries.length > 0 ? (
              <ul className="mt-1 space-y-1">
                {data.search_queries.map((q, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <code className="break-all rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-800">
                      {q}
                    </code>
                    <CopyButton text={q} />
                    <a
                      href={`https://www.google.com/search?q=${encodeURIComponent(q)}`}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      Googleで開く ↗
                    </a>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-1 text-xs text-slate-400">検索候補はまだありません。</p>
            )}
          </div>

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
