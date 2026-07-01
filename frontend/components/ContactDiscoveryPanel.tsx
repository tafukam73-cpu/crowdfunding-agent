"use client";

import { useEffect, useRef, useState } from "react";

import {
  type AiCandidateEmail,
  applyContactPersonToCrm,
  applyDiscoveryToCrm,
  type ContactDiscovery,
  type ContactPerson,
  fetchContactDiscovery,
  fetchContactPeople,
  fetchOutreachMessage,
  formatDateTime,
  type OutreachMessage,
  runAiContactResearch,
  runContactDiscovery,
  runContactHunter,
  runDocumentReader,
  runSearchAgent,
  runWebResearch,
  type SalesContact,
  type ContactIntelligenceJob,
  startContactIntelligenceJob,
  getContactIntelligenceJob,
  getLatestContactIntelligenceJob,
  cancelContactIntelligenceJob,
} from "@/lib/api";

// 営業推奨度の星表示（★★★★★〜★☆☆☆☆）。
function stars(n: number): string {
  const s = Math.max(0, Math.min(5, n));
  return "★".repeat(s) + "☆".repeat(5 - s);
}

const STAR_COLORS: Record<number, string> = {
  5: "text-amber-500",
  4: "text-amber-500",
  3: "text-slate-500",
  2: "text-slate-400",
  1: "text-slate-300",
};

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

// Web調査の発見メール行（自動抽出と同じ DiscoveredEmail 形状）。
type WebEmail = {
  email: string;
  score: number;
  tier: string;
  email_owner?: string | null;
};

// 検索診断（Brave status/理由・成功/0件クエリ・DDGフォールバック・抽出URL件数）。
function SearchDiagnostics({
  diagnostics,
}: {
  diagnostics: NonNullable<ContactDiscovery["web_search_diagnostics"]>;
}) {
  const total = diagnostics.length;
  const ok = diagnostics.filter((d) => (d.results ?? 0) > 0);
  const zero = diagnostics.filter((d) => (d.results ?? 0) === 0);
  const fellBack = diagnostics.filter((d) => d.fallback);
  const urlCount = diagnostics.reduce((n, d) => n + (d.urls?.length ?? 0), 0);
  // 代表的なエラー理由（最初の0件クエリの理由）
  const firstReason = zero.find((d) => d.reason)?.reason;
  const braveStatuses = Array.from(
    new Set(diagnostics.map((d) => d.status).filter((s) => s != null))
  );
  // プロバイダー別集計（成功/0件/エラー理由）
  const provAgg: Record<string, { ok: number; zero: number; reason?: string }> = {};
  for (const d of diagnostics) {
    for (const p of d.providers ?? []) {
      const key = p.provider ?? "?";
      const a = (provAgg[key] ??= { ok: 0, zero: 0 });
      if ((p.results ?? 0) > 0) a.ok += 1;
      else {
        a.zero += 1;
        if (p.reason && !a.reason) a.reason = p.reason;
      }
    }
  }
  const succeededProviders = Object.entries(provAgg)
    .filter(([, a]) => a.ok > 0)
    .map(([p]) => p);
  const zeroProviders = Object.entries(provAgg).filter(
    ([, a]) => a.ok === 0 && a.zero > 0
  );

  return (
    <div className="rounded-md border border-rose-200 bg-rose-50/50 p-2 text-slate-700">
      <p className="font-semibold text-rose-800">🩺 検索診断</p>
      <div className="mt-1 grid grid-cols-2 gap-x-3 gap-y-0.5 sm:grid-cols-3">
        <span>実行クエリ: {total}</span>
        <span className="text-emerald-700">成功: {ok.length}</span>
        <span className="text-slate-500">0件: {zero.length}</span>
        <span>DDGフォールバック: {fellBack.length}</span>
        <span>抽出URL: {urlCount}</span>
        <span>HTTP status: {braveStatuses.join(",") || "-"}</span>
      </div>
      {succeededProviders.length > 0 && (
        <p className="mt-1 text-emerald-700">
          成功プロバイダー: {succeededProviders.join(", ")}
        </p>
      )}
      {zeroProviders.length > 0 && (
        <p className="mt-0.5 text-slate-500">
          0件プロバイダー:{" "}
          {zeroProviders
            .map(([p, a]) => `${p}${a.reason ? `（${a.reason}）` : ""}`)
            .join(" / ")}
        </p>
      )}
      {firstReason && (
        <p className="mt-1 text-rose-700">主な理由: {firstReason}</p>
      )}
      <details className="mt-1">
        <summary className="cursor-pointer text-slate-500">
          クエリ別の詳細（{total}）
        </summary>
        <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto">
          {diagnostics.map((d, i) => (
            <li key={i} className="break-all">
              <span
                className={`mr-1 rounded px-1 text-[10px] ${
                  (d.results ?? 0) > 0
                    ? "bg-emerald-100 text-emerald-700"
                    : "bg-slate-100 text-slate-500"
                }`}
              >
                {d.results ?? 0}件
              </span>
              {d.fallback && (
                <span className="mr-1 rounded bg-amber-100 px-1 text-[10px] text-amber-700">
                  →{d.fallback}
                </span>
              )}
              <code className="text-slate-700">{d.query}</code>
              {d.status != null && (
                <span className="text-slate-400"> [{d.status}]</span>
              )}
              {d.reason && <span className="text-slate-400"> — {d.reason}</span>}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

// slug 検索の実行状況・公式サイト登録状況（NarrationOS 等の可視化）。
function SlugStatus({
  data,
  kw,
}: {
  data: ContactDiscovery;
  kw: NonNullable<ContactDiscovery["web_keyword_candidates"]>;
}) {
  const slugs = [kw.creator_slug, kw.project_slug].filter(Boolean) as string[];
  const executed = data.web_searched_queries ?? [];
  // slug を含む実行クエリがあったか
  const slugRan = executed.filter((q) =>
    slugs.some((s) => q.toLowerCase().includes(s.toLowerCase()))
  );
  // slug 検索由来で採用された結果（social/page）があったか
  const results = data.web_search_results ?? [];
  const slugHits = results.filter(
    (r) =>
      r.adopted &&
      r.query &&
      slugs.some((s) => r.query!.toLowerCase().includes(s.toLowerCase()))
  );
  const dc = data.web_debug_counts;
  const websitesEmpty =
    dc?.ks_websites_present && !dc?.ks_websites_registered;

  return (
    <div className="mt-1 space-y-0.5 rounded bg-slate-50 p-1.5 text-[11px] text-slate-600">
      {websitesEmpty && (
        <p className="text-amber-700">
          Kickstarter websites:[] のため公式サイト未登録（クリエイターが外部サイトを
          登録していません）
        </p>
      )}
      <p>
        slug検索の実行:{" "}
        {slugRan.length > 0 ? (
          <span className="text-teal-700">
            実行済み（{slugRan.length}件）例: {slugRan.slice(0, 3).join(" / ")}
          </span>
        ) : (
          <span className="text-slate-400">未実行</span>
        )}
      </p>
      {slugRan.length > 0 && (
        <p>
          slug検索の成果:{" "}
          {slugHits.length > 0 ? (
            <span className="text-emerald-700">{slugHits.length}件を採用</span>
          ) : (
            <span className="text-slate-500">
              slug検索でも連絡先/公式サイトは未発見
            </span>
          )}
        </p>
      )}
    </div>
  );
}

// 🔍 検索クエリ戦略（どのキーワード/クエリで探し、何を採用/除外したか）。
function SearchStrategyDetails({ data }: { data: ContactDiscovery }) {
  const kw = data.web_keyword_candidates;
  const generated = data.web_generated_queries ?? [];
  const executed = new Set(data.web_searched_queries ?? []);
  const results = data.web_search_results ?? [];

  const hasAnything =
    !!data.web_search_provider ||
    kw ||
    generated.length > 0 ||
    (data.web_searched_queries ?? []).length > 0 ||
    results.length > 0;
  if (!hasAnything) return null;

  // どのクエリで SNS を発見したか（採用された social のみ）。
  const snsHits = results.filter((r) => r.kind === "social" && r.adopted);
  const excluded = results.filter((r) => (r.score ?? 0) < 0 || r.kind === "excluded");
  const socials = Object.keys(data.web_discovered_socials ?? {});

  return (
    <details className="rounded-md border border-teal-200 bg-white/70 p-2 text-xs">
      <summary className="cursor-pointer font-semibold text-teal-800">
        🔍 検索クエリ戦略（キーワード・生成/実行クエリ・採用/除外理由）
      </summary>

      <div className="mt-2 space-y-3">
        {/* 検索診断（検索結果0件の原因究明） */}
        {data.web_search_diagnostics &&
          data.web_search_diagnostics.length > 0 && (
            <SearchDiagnostics diagnostics={data.web_search_diagnostics} />
          )}

        {/* 使用した検索プロバイダー */}
        <p className="text-slate-600">
          使用した検索プロバイダー:{" "}
          <span className="font-semibold text-indigo-700">
            {data.web_search_provider ?? "（未記録）"}
          </span>
          {data.web_search_provider === "duckduckgo" && (
            <span className="text-slate-400">
              {" "}
              （検索API未設定のためフォールバック）
            </span>
          )}
        </p>

        {/* キーワード候補 */}
        {kw && (
          <div>
            <p className="font-semibold text-slate-700">検索キーワード候補</p>
            <ul className="mt-1 space-y-0.5 text-slate-600">
              {kw.project_title && (
                <li>プロジェクト名: {kw.project_title}</li>
              )}
              {kw.short_title && <li>短縮名: {kw.short_title}</li>}
              {kw.maker_name && (
                <li>
                  メーカー名: {kw.maker_name}
                  {kw.maker_ambiguous && (
                    <span className="ml-1 rounded bg-amber-50 px-1 text-[10px] text-amber-700">
                      短く曖昧のため単体検索の優先度を下げ、slug検索を優先
                    </span>
                  )}
                </li>
              )}
              {kw.creator_slug && <li>creator_slug: {kw.creator_slug}</li>}
              {kw.project_slug && <li>project_slug: {kw.project_slug}</li>}
              {kw.brand_names && kw.brand_names.length > 0 && (
                <li>ブランド名候補: {kw.brand_names.join(" / ")}</li>
              )}
              {kw.official_domain && (
                <li>公式ドメイン: {kw.official_domain}</li>
              )}
            </ul>

            {/* slug 検索の実行状況・公式サイト登録状況 */}
            {(kw.creator_slug || kw.project_slug) && (
              <SlugStatus data={data} kw={kw} />
            )}
          </div>
        )}

        {/* 生成クエリ全体 vs 実行済み */}
        {generated.length > 0 && (
          <div>
            <p className="font-semibold text-slate-700">
              生成クエリ {generated.length} 件（うち実行 {executed.size} 件）
            </p>
            <ul className="mt-1 max-h-48 space-y-0.5 overflow-y-auto">
              {generated.map((q, i) => {
                const ran = executed.has(q);
                return (
                  <li key={i} className="flex items-center gap-1">
                    <span
                      className={`rounded px-1 text-[10px] ${
                        ran
                          ? "bg-teal-100 text-teal-700"
                          : "bg-slate-100 text-slate-400"
                      }`}
                    >
                      {ran ? "実行" : "未実行"}
                    </span>
                    <code className="break-all text-slate-700">{q}</code>
                  </li>
                );
              })}
            </ul>
          </div>
        )}

        {/* どのクエリで SNS を発見したか */}
        <div>
          <p className="font-semibold text-slate-700">SNS の発見状況</p>
          {snsHits.length > 0 ? (
            <ul className="mt-1 space-y-0.5 text-slate-600">
              {snsHits.map((r, i) => (
                <li key={i} className="break-all">
                  <span className="mr-1 rounded bg-emerald-100 px-1 text-[10px] text-emerald-700">
                    採用 {r.score}
                  </span>
                  <a
                    href={r.url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-blue-700 hover:underline"
                  >
                    {r.url}
                  </a>
                  {r.query && (
                    <span className="text-slate-400"> ← {r.query}</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="mt-1 text-slate-500">
              {socials.length > 0
                ? "SNS は公式サイト内リンクから取得しました（検索結果からの採用はありません）。"
                : "SNS は発見できませんでした（検索がブロックされたか、複合クエリで該当プロフィールが見つかりませんでした）。"}
            </p>
          )}
        </div>

        {/* 採用された検索結果（除外以外） */}
        {results.filter((r) => r.adopted).length > 0 && (
          <details className="text-slate-500">
            <summary className="cursor-pointer">
              採用した検索結果（{results.filter((r) => r.adopted).length}）
            </summary>
            <ul className="mt-1 space-y-0.5">
              {results
                .filter((r) => r.adopted)
                .map((r, i) => (
                  <li key={i} className="break-all">
                    <span className="mr-1 rounded bg-slate-100 px-1 text-[10px]">
                      {r.kind} {r.score}
                    </span>
                    {r.url}
                    {r.reason && (
                      <span className="text-slate-400"> — {r.reason}</span>
                    )}
                  </li>
                ))}
            </ul>
          </details>
        )}

        {/* 除外した検索結果 */}
        {excluded.length > 0 && (
          <details className="text-slate-500">
            <summary className="cursor-pointer">
              除外した検索結果（{excluded.length}）
            </summary>
            <ul className="mt-1 space-y-0.5">
              {excluded.map((r, i) => (
                <li key={i} className="break-all">
                  <span className="mr-1 rounded bg-red-50 px-1 text-[10px] text-red-600">
                    除外
                  </span>
                  {r.url}
                  {r.reason && (
                    <span className="text-slate-400"> — {r.reason}</span>
                  )}
                </li>
              ))}
            </ul>
          </details>
        )}
      </div>
    </details>
  );
}

// 🔎 じっくり調査（Contact Intelligence v2）。重い探索を非同期ジョブ化してポーリング表示。
function DeepInvestigationSection({
  projectId,
  onDone,
}: {
  projectId: number;
  onDone?: () => void;
}) {
  const [job, setJob] = useState<ContactIntelligenceJob | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pollError, setPollError] = useState<string | null>(null);
  const [stalled, setStalled] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // 同じ current_step が続いた時間の計測（60秒超で「長引いています」表示）
  const stepRef = useRef<{ step: string | null; at: number }>({
    step: null,
    at: Date.now(),
  });
  const failRef = useRef(0);

  const ACTIVE = (s?: string) => s === "queued" || s === "running";

  function stopPolling() {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  }

  function startPolling(jobId: number) {
    stopPolling();
    stepRef.current = { step: null, at: Date.now() };
    failRef.current = 0;
    setStalled(false);
    setPollError(null);
    pollRef.current = setInterval(async () => {
      try {
        const j = await getContactIntelligenceJob(jobId);
        failRef.current = 0;
        setPollError(null);
        setJob(j);
        // ステップが変わったら計測をリセット。同じステップが 60 秒超で「長引き」表示。
        if (j.current_step !== stepRef.current.step) {
          stepRef.current = { step: j.current_step, at: Date.now() };
          setStalled(false);
        } else if (ACTIVE(j.status) && Date.now() - stepRef.current.at > 60000) {
          setStalled(true);
        }
        if (!ACTIVE(j.status)) {
          stopPolling();
          setStalled(false);
          if (j.status === "completed") onDone?.();
        }
      } catch (e) {
        // 連続失敗時のみエラー表示（一時的な失敗は握りつぶす）
        failRef.current += 1;
        if (failRef.current >= 3) {
          setPollError(
            "進捗の取得に繰り返し失敗しています（ネットワーク/サーバを確認してください）。"
          );
        }
      }
    }, 2000);
  }

  // 初回：最新の full ジョブを取得。進行中なら購読、完了済みならキャッシュ提示。
  useEffect(() => {
    let active = true;
    getLatestContactIntelligenceJob(projectId, "full_contact_intelligence")
      .then((j) => {
        if (!active || !j) return;
        setJob(j);
        if (ACTIVE(j.status)) startPolling(j.id);
      })
      .catch(() => {});
    return () => {
      active = false;
      stopPolling();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function start(force: boolean) {
    setBusy(true);
    setError(null);
    try {
      const j = await startContactIntelligenceJob(
        projectId,
        "full_contact_intelligence",
        force
      );
      setJob(j);
      if (ACTIVE(j.status)) startPolling(j.id);
      else if (j.status === "completed") onDone?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onCancel() {
    if (!job) return;
    try {
      setJob(await cancelContactIntelligenceJob(job.id));
    } catch (e) {
      setError(String(e));
    }
  }

  const active = ACTIVE(job?.status);
  const completedRecent =
    job?.status === "completed" &&
    job.completed_at &&
    Date.now() - new Date(job.completed_at).getTime() < 24 * 3600 * 1000;
  const result = (job?.result_json ?? {}) as Record<string, unknown>;

  return (
    <div className="rounded-md border-2 border-indigo-300 bg-indigo-50/70 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-bold text-indigo-900">🔎 じっくり調査（推奨）</p>
          <p className="mt-0.5 text-xs text-indigo-700">
            Web調査 → Document Reader → Search Agent →
            営業推奨ランキング更新を、バックグラウンドでまとめて実行します（重い探索でも
            タイムアウトしません。進捗はここに表示されます）。
          </p>
        </div>
        <div className="flex items-center gap-2">
          {active ? (
            <button
              onClick={onCancel}
              className="rounded border border-red-300 bg-red-50 px-3 py-1.5 text-sm font-medium text-red-700 hover:bg-red-100"
            >
              中断
            </button>
          ) : (
            <>
              {completedRecent && (
                <button
                  onClick={() => onDone?.()}
                  className="rounded border border-indigo-300 bg-white px-3 py-1.5 text-sm text-indigo-700 hover:bg-indigo-50"
                >
                  前回結果を使う
                </button>
              )}
              <button
                onClick={() => start(true)}
                disabled={busy}
                className="rounded bg-indigo-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-indigo-600 disabled:opacity-50"
              >
                {busy
                  ? "開始中…"
                  : completedRecent
                  ? "再実行"
                  : "じっくり調査を開始"}
              </button>
            </>
          )}
        </div>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {job && (
        <div className="mt-3 space-y-2">
          {/* 進捗バー */}
          <div className="flex items-center gap-2">
            <div className="h-2 flex-1 overflow-hidden rounded-full bg-indigo-100">
              <div
                className={`h-full rounded-full transition-all ${
                  job.status === "failed"
                    ? "bg-red-500"
                    : job.status === "cancelled"
                    ? "bg-slate-400"
                    : "bg-indigo-600"
                }`}
                style={{ width: `${job.progress ?? 0}%` }}
              />
            </div>
            <span className="text-xs font-semibold text-indigo-800">
              {job.progress ?? 0}%
            </span>
          </div>
          <p className="text-xs text-slate-600">
            <span className="font-semibold">状態:</span> {job.status}
            {job.current_step ? ` / ${job.current_step}` : ""}
            {job.from_cache && "（前回結果／キャッシュ）"}
          </p>
          {stalled && ACTIVE(job.status) && (
            <p className="rounded bg-amber-50 px-2 py-1 text-xs text-amber-700">
              ⏳ 処理が長引いています（重いページの取得中かもしれません）。このまま
              お待ちいただくか、「中断」できます。
            </p>
          )}
          {pollError && <p className="text-xs text-red-600">{pollError}</p>}
          {job.status === "failed" && (
            <p className="text-xs text-red-600 break-all">
              失敗：{job.error ?? "原因不明のエラー"}
            </p>
          )}
          {job.status === "cancelled" && (
            <p className="text-xs text-slate-500">中断しました。</p>
          )}

          {/* 結果サマリ */}
          {job.status === "completed" && (
            <div className="rounded-md border border-indigo-200 bg-white p-2 text-xs text-slate-700">
              <span className="font-semibold text-indigo-800">結果:</span>{" "}
              公式サイト {String(result.official_site_url ?? "未発見")} / 推奨連絡先{" "}
              {result.top_contact
                ? String(
                    (result.top_contact as Record<string, unknown>).email ?? "-"
                  )
                : "-"}{" "}
              / 連絡先候補 {String(result.sales_contacts_count ?? 0)}件 / SNS{" "}
              {Object.keys(
                (result.socials as Record<string, unknown>) ?? {}
              ).length}
              件
            </div>
          )}

          {/* ログ（最新6件） */}
          {job.logs_json && job.logs_json.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                進捗ログ（{job.logs_json.length}）
              </summary>
              <ul className="mt-1 max-h-40 space-y-0.5 overflow-y-auto">
                {job.logs_json.slice(-12).map((l, i) => (
                  <li key={i} className="break-all">
                    {l.message}
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

// 🏆 営業推奨連絡先（発見メールを営業のしやすさ順に格付け）。Contact Intelligence 最上部。
function SalesRankingSection({
  projectId,
  contacts,
  onApply,
}: {
  projectId: number;
  contacts: SalesContact[];
  onApply: (email: string) => void;
}) {
  const [gmailMsg, setGmailMsg] = useState<string | null>(null);

  function useAsGmailTo(email: string) {
    try {
      sessionStorage.setItem(gmailToKey(projectId), email);
    } catch {
      /* sessionStorage 不可環境では無視 */
    }
    setGmailMsg(`「${email}」をメール作成画面（STEP 3）の宛先候補に設定しました。`);
  }

  if (!contacts || contacts.length === 0) return null;
  const top = contacts[0];

  return (
    <div className="rounded-md border-2 border-amber-300 bg-amber-50/70 p-4">
      <p className="text-sm font-bold text-amber-900">🏆 営業推奨連絡先</p>
      <p className="mt-0.5 text-xs text-amber-700">
        発見したメールを「営業のしやすさ」で自動ランキングしました（最上位を推奨）。
      </p>

      {/* 最上位（推奨送信先） */}
      <div className="mt-3 rounded-md border border-amber-300 bg-white p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className={`text-base font-bold ${STAR_COLORS[top.stars]}`}>
            {stars(top.stars)}
          </span>
          <span className="rounded bg-amber-200 px-2 py-0.5 text-xs font-bold text-amber-900">
            推奨
          </span>
          <a
            href={`mailto:${top.email}`}
            className="font-semibold text-slate-900 hover:underline"
          >
            {top.email}
          </a>
          <CopyButton text={top.email} label="コピー" />
        </div>
        <p className="mt-1 text-xs text-slate-600">理由：{top.reason}</p>
        <div className="mt-2 flex flex-wrap items-center gap-2">
          <button
            onClick={() => useAsGmailTo(top.email)}
            className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
          >
            Gmail宛先に使用（最上位）
          </button>
          <button
            onClick={() => onApply(top.email)}
            className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
          >
            CRMに反映
          </button>
        </div>
      </div>

      {gmailMsg && (
        <p className="mt-2 text-xs font-medium text-blue-700">{gmailMsg}</p>
      )}

      {/* 残りの候補（順位つき） */}
      {contacts.length > 1 && (
        <ul className="mt-3 space-y-1">
          {contacts.slice(1).map((c) => (
            <li
              key={c.email}
              className="flex flex-wrap items-center gap-2 border-t border-amber-200 pt-1 text-sm"
            >
              <span className={`font-semibold ${STAR_COLORS[c.stars]}`}>
                {stars(c.stars)}
              </span>
              <span className="text-slate-800">{c.email}</span>
              <span className="text-xs text-slate-500">— {c.reason}</span>
              <CopyButton text={c.email} />
              <button
                onClick={() => useAsGmailTo(c.email)}
                className="rounded border border-blue-200 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
              >
                Gmail宛先
              </button>
              <button
                onClick={() => onApply(c.email)}
                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                CRM
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

// 🕵️ AI Search Agent（次に見るページを判断しながら反復探索）。
function SearchAgentSection({
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
    setGmailMsg(`「${email}」をメール作成画面（STEP 3）の宛先候補に設定しました。`);
  }

  const researched = data?.search_agent_researched;
  const failed = data?.search_agent_status === "failed";
  const steps = data?.search_agent_steps ?? [];
  const emails = data?.search_agent_emails ?? [];
  const forms = data?.search_agent_contact_forms ?? [];
  const socials = Object.entries(data?.search_agent_socials ?? {});
  const people = data?.search_agent_people ?? [];

  const ACTION_LABEL: Record<string, string> = {
    search: "🔍 検索",
    visit: "🌐 取得",
    skip: "⏭ スキップ",
    stop: "⏹ 終了",
  };

  return (
    <div className="rounded-md border border-cyan-300 bg-cyan-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-cyan-900">🕵️ AI Search Agent</p>
          <p className="mt-0.5 text-xs text-cyan-700">
            AIが「次に見るべきページ・検索クエリ」を毎ステップ判断し、SNS→Linktree→
            公式サイト→Contact のようにリンクを辿って連絡先を探します（最大5ステップ。
            推測メールは作りません）。
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-cyan-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-cyan-600 disabled:opacity-50"
        >
          {busy ? "探索中…" : researched ? "AIで再探索" : "AIで探索する"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {!researched && !error && (
        <p className="mt-3 text-xs text-cyan-700">
          まだ探索していません。上のボタンで、AIがページを辿って連絡先を探索します
          （ANTHROPIC_API_KEY 未設定時はモックで動作）。
        </p>
      )}

      {researched && data && (
        <div className="mt-3 space-y-3 text-sm">
          {failed && (
            <div className="rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
              <p className="font-semibold">探索でエラーが発生しました。</p>
              <p className="mt-1 whitespace-pre-wrap break-all">
                {data.search_agent_error}
              </p>
            </div>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {data.search_agent_model && (
              <span className="rounded bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-800">
                モデル: {data.search_agent_model}
              </span>
            )}
            <span className="rounded bg-cyan-100 px-2 py-0.5 text-xs font-medium text-cyan-800">
              確度: {data.search_agent_confidence_score ?? 0} / 100
            </span>
            {data.search_agent_recommended_channel && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                推奨チャネル:{" "}
                {CHANNEL_LABELS[data.search_agent_recommended_channel] ??
                  data.search_agent_recommended_channel}
              </span>
            )}
          </div>

          {data.search_agent_evidence_summary && (
            <div className="rounded-md border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
              {data.search_agent_evidence_summary}
            </div>
          )}

          {/* 推奨連絡先 */}
          {data.search_agent_recommended_contact && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-cyan-700">推奨送信先</span>
              <span className="font-medium text-slate-900">
                {data.search_agent_recommended_contact}
              </span>
              <CopyButton text={data.search_agent_recommended_contact} label="コピー" />
              <button
                onClick={() =>
                  onUseAsGmailTo(data.search_agent_recommended_contact as string)
                }
                className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
              >
                Gmail宛先に使用
              </button>
              <button
                onClick={() =>
                  onApply(data.search_agent_recommended_contact as string)
                }
                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                CRMに反映
              </button>
            </div>
          )}

          {/* 探索ステップ */}
          {steps.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-cyan-700">
                探索ステップ（{steps.length}）
              </p>
              <ol className="mt-1 space-y-0.5">
                {steps.map((s, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-1 text-xs">
                    <span className="rounded bg-cyan-100 px-1 text-[10px] text-cyan-800">
                      #{s.step}
                    </span>
                    <span className="text-slate-600">
                      {ACTION_LABEL[s.action ?? ""] ?? s.action}
                    </span>
                    {s.query && <code className="break-all text-slate-700">{s.query}</code>}
                    {s.url && (
                      <span className="break-all text-slate-500">
                        {s.url}
                        {s.ok === false && " (取得失敗)"}
                        {s.found &&
                          Object.values(s.found).some((v) => v > 0) &&
                          ` → メール${s.found.emails ?? 0}/SNS${s.found.socials ?? 0}`}
                      </span>
                    )}
                    {s.action === "stop" && s.reason && (
                      <span className="text-slate-500">— {s.reason}</span>
                    )}
                  </li>
                ))}
              </ol>
            </div>
          )}

          {/* 発見メール */}
          {emails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-cyan-700">
                発見メール（出典付き・再検証済み）
              </p>
              <ul className="mt-1 space-y-1">
                {emails.map((e) => (
                  <li key={e.email} className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-cyan-100 px-2 py-0.5 text-xs text-cyan-800">
                      {e.purpose ?? "other"} {e.confidence}
                    </span>
                    <span className="text-slate-800">{e.email}</span>
                    <CopyButton text={e.email} />
                    <button
                      onClick={() => onUseAsGmailTo(e.email)}
                      className="rounded border border-blue-200 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
                    >
                      Gmail宛先
                    </button>
                    <button
                      onClick={() => onApply(e.email)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      CRM
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* フォーム / SNS */}
          {(forms.length > 0 || socials.length > 0) && (
            <div className="flex flex-wrap items-center gap-3">
              {forms.map((f) => (
                <a
                  key={f.url}
                  href={f.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  問い合わせフォームを開く ↗
                </a>
              ))}
              {socials.map(([platform, url]) => (
                <a
                  key={platform}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  {platform} ↗
                </a>
              ))}
            </div>
          )}

          {/* 担当者 */}
          {people.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-cyan-700">担当者候補</p>
              <ul className="mt-1 space-y-1">
                {people.map((p, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{p.name}</span>
                    {p.title && <span className="text-xs text-slate-500">{p.title}</span>}
                    {p.linkedin_url && (
                      <a
                        href={p.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-blue-700 hover:underline"
                      >
                        LinkedIn ↗
                      </a>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {gmailMsg && (
            <p className="text-xs font-medium text-blue-700">{gmailMsg}</p>
          )}

          {data.search_agent_stop_reason && (
            <p className="text-xs text-slate-500">
              <span className="font-semibold">終了理由：</span>
              {data.search_agent_stop_reason}
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// 🧠 AI Document Reader（ページ全体を読解して連絡先を文脈から整理）。
function DocumentReaderSection({
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
    setGmailMsg(`「${email}」をメール作成画面（STEP 3）の宛先候補に設定しました。`);
  }

  const researched = data?.doc_reader_researched;
  const emails = data?.doc_reader_emails ?? [];
  const forms = data?.doc_reader_contact_forms ?? [];
  const socials = Object.entries(data?.doc_reader_socials ?? {});
  const people = data?.doc_reader_people ?? [];
  const sources = data?.doc_reader_sources ?? [];

  return (
    <div className="rounded-md border border-violet-300 bg-violet-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-violet-900">
            🧠 AI Document Reader
          </p>
          <p className="mt-0.5 text-xs text-violet-700">
            取得済みページ（案件・プロフィール・公式サイト・Contact/About/Team/Press
            等）の本文・リンク・PDF・検索スニペットをAIが読解し、会社名・公式サイト・
            メール・SNS・フォーム・担当者候補を文脈から整理します（推測メール・人名は
            作りません）。
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-violet-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-violet-600 disabled:opacity-50"
        >
          {busy ? "読解中…" : researched ? "AIで再読解" : "AIでページを読解"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {!researched && !error && (
        <p className="mt-3 text-xs text-violet-700">
          まだ読解していません。上のボタンで、取得済みページをAIに読ませて連絡先を
          整理できます（ANTHROPIC_API_KEY 未設定時はモックで動作）。
        </p>
      )}

      {researched && data && (
        <div className="mt-3 space-y-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            {data.doc_reader_model && (
              <span className="rounded bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-800">
                モデル: {data.doc_reader_model}
              </span>
            )}
            <span className="rounded bg-violet-100 px-2 py-0.5 text-xs font-medium text-violet-800">
              確度: {data.doc_reader_confidence_score ?? 0} / 100
            </span>
            {data.doc_reader_recommended_channel && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                推奨チャネル:{" "}
                {CHANNEL_LABELS[data.doc_reader_recommended_channel] ??
                  data.doc_reader_recommended_channel}
              </span>
            )}
          </div>

          {(data.doc_reader_official_company_name ||
            data.doc_reader_official_site_url) && (
            <div className="text-xs text-slate-700">
              {data.doc_reader_official_company_name && (
                <span className="mr-2">
                  <span className="text-slate-400">会社名：</span>
                  {data.doc_reader_official_company_name}
                </span>
              )}
              <span className="text-slate-400">公式サイト：</span>
              {data.doc_reader_official_site_url ? (
                <a
                  href={data.doc_reader_official_site_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-blue-700 hover:underline"
                >
                  {data.doc_reader_official_site_url.replace(/^https?:\/\//, "")}
                </a>
              ) : (
                <span className="text-slate-400">未発見</span>
              )}
            </div>
          )}

          {data.doc_reader_evidence_summary && (
            <div className="rounded-md border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
              {data.doc_reader_evidence_summary}
            </div>
          )}

          {/* 推奨連絡先 */}
          {data.doc_reader_recommended_contact && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs font-semibold text-violet-700">推奨送信先</span>
              <span className="font-medium text-slate-900">
                {data.doc_reader_recommended_contact}
              </span>
              <CopyButton text={data.doc_reader_recommended_contact} label="コピー" />
              <button
                onClick={() =>
                  onUseAsGmailTo(data.doc_reader_recommended_contact as string)
                }
                className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
              >
                Gmail宛先に使用
              </button>
              <button
                onClick={() => onApply(data.doc_reader_recommended_contact as string)}
                className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
              >
                CRMに反映
              </button>
            </div>
          )}

          {/* 発見メール */}
          {emails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-violet-700">
                読解で発見したメール（出典付き・再検証済み）
              </p>
              <ul className="mt-1 space-y-1">
                {emails.map((e) => (
                  <li key={e.email} className="flex flex-wrap items-center gap-2">
                    <span className="rounded bg-violet-100 px-2 py-0.5 text-xs text-violet-800">
                      {e.purpose ?? "other"} {e.confidence}
                    </span>
                    <span className="text-slate-800">{e.email}</span>
                    <CopyButton text={e.email} />
                    <button
                      onClick={() => onUseAsGmailTo(e.email)}
                      className="rounded border border-blue-200 px-2 py-0.5 text-xs text-blue-700 hover:bg-blue-50"
                    >
                      Gmail宛先
                    </button>
                    <button
                      onClick={() => onApply(e.email)}
                      className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                    >
                      CRM
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* フォーム / SNS */}
          {(forms.length > 0 || socials.length > 0) && (
            <div className="flex flex-wrap items-center gap-3">
              {forms.map((f) => (
                <a
                  key={f.url}
                  href={f.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  問い合わせフォームを開く ↗
                </a>
              ))}
              {socials.map(([platform, url]) => (
                <a
                  key={platform}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  {platform} ↗
                </a>
              ))}
            </div>
          )}

          {/* 担当者候補 */}
          {people.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-violet-700">担当者候補</p>
              <ul className="mt-1 space-y-1">
                {people.map((p, i) => (
                  <li key={i} className="flex flex-wrap items-center gap-2">
                    <span className="font-medium text-slate-800">{p.name}</span>
                    {p.title && (
                      <span className="text-xs text-slate-500">{p.title}</span>
                    )}
                    {p.linkedin_url && (
                      <a
                        href={p.linkedin_url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-xs text-blue-700 hover:underline"
                      >
                        LinkedIn ↗
                      </a>
                    )}
                    {p.email && (
                      <>
                        <span className="text-xs text-slate-700">{p.email}</span>
                        <CopyButton text={p.email} />
                      </>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {gmailMsg && (
            <p className="text-xs font-medium text-blue-700">{gmailMsg}</p>
          )}

          {/* 不足情報 */}
          {data.doc_reader_missing_info &&
            data.doc_reader_missing_info.length > 0 && (
              <div className="text-xs text-slate-500">
                <p className="font-semibold">不足情報</p>
                <ul className="mt-0.5 list-disc pl-4">
                  {data.doc_reader_missing_info.map((m, i) => (
                    <li key={i}>{m}</li>
                  ))}
                </ul>
              </div>
            )}

          {/* 参照ページ */}
          {sources.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                読解した参照ページ（{sources.length}）
              </summary>
              <ul className="mt-1 space-y-0.5">
                {sources.map((s, i) => (
                  <li key={i} className="break-all">
                    {s.type && (
                      <span className="mr-1 rounded bg-slate-100 px-1 text-[10px]">
                        {s.type}
                      </span>
                    )}
                    <a
                      href={s.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 hover:underline"
                    >
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

// AI Web Research Mode（検索エンジン＋公式サイト横断クロールの実調査）。
function WebResearchSection({
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

  const researched = data?.web_researched;
  const failed = !!data?.web_research_error;
  // 運営会社（platform）のメールは営業先ではないため非表示
  const emails = ((data?.web_discovered_emails ?? []) as WebEmail[]).filter(
    (e) => e.email_owner !== "platform"
  );
  const socials = Object.entries(data?.web_discovered_socials ?? {});
  const pdfs = data?.web_discovered_pdfs ?? [];

  return (
    <div className="rounded-md border border-teal-300 bg-teal-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-teal-900">
            🌐 AI Web調査（Web Research Mode）
          </p>
          <p className="mt-0.5 text-xs text-teal-700">
            検索エンジン（DuckDuckGo）＋公式サイト・Contact・About・Press・Wholesale・
            SNS・PDFを横断調査し、実際に取得したページから連絡先を抽出します（推測
            メールは作らず、出典付きのみ採用）。
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-teal-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-teal-600 disabled:opacity-50"
        >
          {busy ? "Web調査中…" : researched ? "AI Web再調査" : "AI Web調査を実行"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {!researched && !error && (
        <p className="mt-3 text-xs text-teal-700">
          まだWeb調査は実行されていません。上のボタンで実行できます（検索エンジンに
          ブロックされた場合は公式サイト探索のみで継続します）。
        </p>
      )}

      {researched && data && (
        <div className="mt-3 space-y-3 text-sm">
          {failed && (
            <div className="rounded-md border border-red-200 bg-red-50 p-2 text-xs text-red-700">
              <p className="font-semibold">Web調査でエラーが発生しました。</p>
              <p className="mt-1 whitespace-pre-wrap break-all">
                {data.web_research_error}
              </p>
            </div>
          )}

          {/* 確度 & 推奨チャネル */}
          <div className="flex flex-wrap items-center gap-2">
            {data.web_search_provider && (
              <span className="rounded bg-indigo-100 px-2 py-0.5 text-xs font-medium text-indigo-700">
                検索: {data.web_search_provider}
              </span>
            )}
            <span className="rounded bg-teal-100 px-2 py-0.5 text-xs font-medium text-teal-800">
              Web確度: {data.web_confidence_score ?? 0} / 100
            </span>
            {data.web_recommended_channel && (
              <span className="rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
                Web推奨チャネル:{" "}
                {CHANNEL_LABELS[data.web_recommended_channel] ??
                  data.web_recommended_channel}
              </span>
            )}
          </div>

          {/* 探索フロー（要件5）＋ デバッグ集計（要件6） */}
          {data.web_research_flow && (
            <div className="rounded-md border border-slate-200 bg-white p-2 text-xs text-slate-600">
              <span className="font-semibold text-slate-700">探索フロー:</span>{" "}
              {data.web_research_flow}
            </div>
          )}
          {data.web_debug_counts && (
            <div className="grid grid-cols-2 gap-1 rounded-md border border-slate-200 bg-white p-2 text-xs text-slate-600 sm:grid-cols-4">
              {(
                [
                  ["検索クエリ数", data.web_debug_counts.queries],
                  ["検索結果件数", data.web_debug_counts.results],
                  ["巡回URL数", data.web_debug_counts.crawled],
                  ["成功URL数", data.web_debug_counts.ok],
                  ["失敗URL数", data.web_debug_counts.failed],
                  ["除外URL数", data.web_debug_counts.excluded],
                  ["メール抽出対象", data.web_debug_counts.email_pages],
                ] as [string, number | null][]
              ).map(([label, val]) => (
                <span key={label} className="flex items-center justify-between gap-1">
                  <span className="text-slate-400">{label}</span>
                  <span className="font-semibold text-slate-700">{val ?? 0}</span>
                </span>
              ))}
            </div>
          )}

          {/* Kickstarter 等の埋め込み websites 配列の状況（公式サイト未登録の根拠） */}
          {data.web_debug_counts?.ks_websites_present && (
            <div className="rounded-md border border-slate-200 bg-white p-2 text-xs">
              <span className="font-semibold text-slate-600">
                Kickstarter websites 配列：
              </span>{" "}
              <span className="text-slate-700">
                あり（{data.web_debug_counts.ks_websites_count ?? 0} 件）
              </span>
              {data.web_debug_counts.ks_websites_registered ? (
                <span className="ml-1 text-emerald-700">公式サイト登録あり</span>
              ) : (
                <span className="ml-1 rounded bg-amber-50 px-1 text-amber-700">
                  公式サイト未登録（クリエイターが外部サイトを登録していません）
                </span>
              )}
            </div>
          )}

          {data.web_evidence_summary && (
            <div className="rounded-md border border-sky-200 bg-sky-50 p-2 text-xs text-sky-900">
              {data.web_evidence_summary}
            </div>
          )}
          {data.web_notes && (
            <p className="text-xs text-slate-500">{data.web_notes}</p>
          )}

          {/* Web primary email */}
          {data.web_primary_email ? (
            <div>
              <p className="text-xs font-semibold text-teal-700">
                Web主要メール（出典付き・再検証済み）
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="font-medium text-slate-900">
                  {data.web_primary_email}
                </span>
                <CopyButton text={data.web_primary_email} label="コピー" />
                <button
                  onClick={() => onApply(data.web_primary_email as string)}
                  className="rounded border border-slate-300 px-2 py-0.5 text-xs text-slate-600 hover:bg-slate-50"
                >
                  CRMに反映
                </button>
                <button
                  onClick={() => onUseAsGmailTo(data.web_primary_email as string)}
                  className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                >
                  Gmail宛先に使用
                </button>
              </div>
            </div>
          ) : (
            <p className="text-xs text-slate-500">
              Web調査では出典付きの確実なメールを発見できませんでした。下記の推奨
              チャネル・フォーム・SNS・検索クエリで営業先を確保してください。
            </p>
          )}

          {gmailMsg && (
            <p className="text-xs font-medium text-blue-700">{gmailMsg}</p>
          )}

          {/* 発見メール一覧 */}
          {emails.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-teal-700">
                発見メール（優先度順・出典付き）
              </p>
              <ul className="mt-1 space-y-1">
                {emails.map((e) => (
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
                    <button
                      onClick={() => onUseAsGmailTo(e.email)}
                      className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                    >
                      Gmail宛先に使用
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* フォーム / SNS */}
          {(data.web_primary_contact_form_url || socials.length > 0) && (
            <div className="flex flex-wrap items-center gap-3">
              {data.web_primary_contact_form_url && (
                <a
                  href={data.web_primary_contact_form_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  問い合わせフォームを開く ↗
                </a>
              )}
              {socials.map(([platform, url]) => (
                <a
                  key={platform}
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs text-blue-700 hover:underline"
                >
                  {platform} ↗
                </a>
              ))}
            </div>
          )}

          {/* PDF */}
          {pdfs.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-teal-700">PDFリンク</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                {pdfs.map((p, i) => (
                  <li key={i}>
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noreferrer"
                      className="break-all text-blue-700 hover:underline"
                    >
                      {p.label ?? p.url} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* 検索クエリ戦略（折りたたみ：キーワード・生成/実行・採用/除外理由） */}
          <SearchStrategyDetails data={data} />

          {/* 検索クエリ */}
          {data.web_searched_queries && data.web_searched_queries.length > 0 && (
            <div>
              <p className="text-xs font-semibold text-teal-700">
                調査した検索クエリ
              </p>
              <ul className="mt-1 space-y-1">
                {data.web_searched_queries.map((q, i) => (
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

          {/* 調査した候補ページ */}
          {data.web_candidate_pages && data.web_candidate_pages.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                調査した候補ページ（{data.web_candidate_pages.length}）
              </summary>
              <ul className="mt-1 space-y-0.5">
                {data.web_candidate_pages.map((p, i) => (
                  <li key={i} className="break-all">
                    <span
                      className={`mr-1 rounded px-1 text-[10px] ${
                        p.ok === false
                          ? "bg-red-50 text-red-600"
                          : "bg-emerald-50 text-emerald-600"
                      }`}
                    >
                      {p.ok === false ? "失敗" : "成功"}
                    </span>
                    {p.type && (
                      <span className="mr-1 rounded bg-slate-100 px-1 text-[10px] text-slate-600">
                        {p.type}
                      </span>
                    )}
                    {typeof p.emails === "number" && p.emails > 0 && (
                      <span className="mr-1 rounded bg-amber-50 px-1 text-[10px] text-amber-700">
                        メール{p.emails}
                      </span>
                    )}
                    <a
                      href={p.url}
                      target="_blank"
                      rel="noreferrer"
                      className="text-blue-700 hover:underline"
                    >
                      {p.url} ↗
                    </a>
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* 探索した URL（出典） */}
          {data.web_searched_urls && data.web_searched_urls.length > 0 && (
            <details className="text-xs text-slate-500">
              <summary className="cursor-pointer">
                探索した URL（{data.web_searched_urls.length}）
              </summary>
              <ul className="mt-1 list-disc space-y-0.5 pl-4">
                {data.web_searched_urls.map((u, i) => (
                  <li key={i} className="break-all">
                    {u}
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

// 営業優先度バンドの色（高=緑 / 中=琥珀 / 低=灰）。
function priorityClass(priority: number | null): string {
  const p = priority ?? 0;
  if (p >= 85) return "bg-emerald-100 text-emerald-700";
  if (p >= 70) return "bg-amber-100 text-amber-700";
  if (p <= 40) return "bg-slate-200 text-slate-500";
  return "bg-sky-100 text-sky-700";
}

// 👤 Contact Hunter（営業担当者の発見）。会社ではなく「誰に送るか」を出典付きで特定。
function ContactHunterSection({
  projectId,
  onChanged,
}: {
  projectId: number;
  onChanged?: () => void;
}) {
  const [people, setPeople] = useState<ContactPerson[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    fetchContactPeople(projectId)
      .then(setPeople)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    setMsg(null);
    try {
      setPeople(await runContactHunter(projectId));
      onChanged?.();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onAddToCrm(p: ContactPerson) {
    setMsg(null);
    try {
      const r = await applyContactPersonToCrm(projectId, p.id);
      setMsg(`CRMに担当者を追加しました：${r.name ?? "(無名)"}`);
      onChanged?.();
    } catch (e) {
      setMsg(`CRM反映に失敗しました：${String(e)}`);
    }
  }

  function onUseAsGmailTo(email: string) {
    try {
      sessionStorage.setItem(gmailToKey(projectId), email);
    } catch {
      /* sessionStorage 不可環境では無視 */
    }
    setMsg(`「${email}」をメール作成画面（STEP 3）の宛先候補に設定しました。`);
  }

  const hasRun = people !== null;

  return (
    <div className="rounded-md border border-rose-300 bg-rose-50/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <p className="text-sm font-semibold text-rose-900">
            👤 Contact Hunter（営業担当者の発見）
          </p>
          <p className="mt-0.5 text-xs text-rose-700">
            会社ではなく「誰に送るか」を特定します。Business Development / Partnership /
            Export / Sales / Founder などを、公式サイトのTeam・About・Leadership・
            LinkedInから出典付きで探します（推測の人名は作りません）。
          </p>
        </div>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-rose-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-rose-600 disabled:opacity-50"
        >
          {busy ? "担当者を調査中…" : hasRun ? "担当者を再調査" : "担当者を探す"}
        </button>
      </div>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {msg && <p className="mt-2 text-xs font-medium text-blue-700">{msg}</p>}

      {hasRun && people.length === 0 && !error && (
        <p className="mt-3 text-xs text-rose-700">
          担当者は特定できませんでした（公式サイトのチーム/会社情報やLinkedInが非公開の
          可能性）。推測の人名は作成していません。メール・フォーム・SNS（上のセクション）で
          営業先を確保してください。
        </p>
      )}

      {hasRun && people.length > 0 && (
        <ul className="mt-3 space-y-2">
          {people.map((p) => (
            <li
              key={p.id}
              className="rounded border border-rose-200 bg-white p-3 text-sm"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span
                  className={`rounded px-2 py-0.5 text-xs font-semibold ${priorityClass(
                    p.priority
                  )}`}
                >
                  優先度 {p.priority ?? 0}
                </span>
                {p.department && (
                  <span className="rounded bg-rose-100 px-2 py-0.5 text-xs font-medium text-rose-700">
                    {p.department}
                  </span>
                )}
                <span className="font-semibold text-slate-900">
                  {p.name ?? "(氏名不明)"}
                </span>
                {p.title && (
                  <span className="text-xs text-slate-600">{p.title}</span>
                )}
                {p.confidence != null && (
                  <span className="text-xs text-slate-400">
                    信頼度 {p.confidence}
                  </span>
                )}
              </div>

              {p.email && (
                <p className="mt-1 text-xs text-slate-700">
                  メール: <span className="font-medium">{p.email}</span>
                </p>
              )}

              <div className="mt-2 flex flex-wrap items-center gap-2">
                {p.linkedin_url && (
                  <a
                    href={p.linkedin_url}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                  >
                    LinkedInを開く ↗
                  </a>
                )}
                {p.email && <CopyButton text={p.email} label="メールをコピー" />}
                {p.email && (
                  <button
                    onClick={() => onUseAsGmailTo(p.email as string)}
                    className="rounded border border-blue-300 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 hover:bg-blue-100"
                  >
                    Gmail宛先
                  </button>
                )}
                <button
                  onClick={() => onAddToCrm(p)}
                  className="rounded border border-emerald-300 bg-emerald-50 px-2 py-0.5 text-xs font-medium text-emerald-700 hover:bg-emerald-100"
                >
                  CRMへ追加
                </button>
                {p.source_url && (
                  <a
                    href={p.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-xs text-slate-500 hover:underline"
                  >
                    出典 ↗
                  </a>
                )}
              </div>
            </li>
          ))}
        </ul>
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
  const [webBusy, setWebBusy] = useState(false);
  const [webError, setWebError] = useState<string | null>(null);
  const [docBusy, setDocBusy] = useState(false);
  const [docError, setDocError] = useState<string | null>(null);
  const [agentBusy, setAgentBusy] = useState(false);
  const [agentError, setAgentError] = useState<string | null>(null);

  useEffect(() => {
    fetchContactDiscovery(projectId)
      .then(setData)
      .catch((e) => setError(String(e)));
  }, [projectId]);

  // じっくり調査ジョブ完了時などに最新の探索結果を取り込む。
  function refetchDiscovery() {
    fetchContactDiscovery(projectId)
      .then((d) => {
        setData(d);
        onChanged?.();
      })
      .catch(() => {});
  }

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

  async function onRunWeb() {
    setWebBusy(true);
    setWebError(null);
    setApplyMsg(null);
    try {
      // Web 調査は最新の探索結果（web_* 含む）を返す。土台が無ければサーバ側で
      // 自動探索を先に実行する。
      setData(await runWebResearch(projectId));
      onChanged?.();
    } catch (e) {
      setWebError(String(e));
    } finally {
      setWebBusy(false);
    }
  }

  async function onRunDoc() {
    setDocBusy(true);
    setDocError(null);
    setApplyMsg(null);
    try {
      // AI Document Reader は最新の探索結果（doc_reader_* 含む）を返す。土台が
      // 無ければサーバ側で自動探索を先に実行する。
      setData(await runDocumentReader(projectId));
      onChanged?.();
    } catch (e) {
      setDocError(String(e));
    } finally {
      setDocBusy(false);
    }
  }

  async function onRunAgent() {
    setAgentBusy(true);
    setAgentError(null);
    setApplyMsg(null);
    try {
      // Search Agent は最新の探索結果（search_agent_* 含む）を返す。土台が無ければ
      // サーバ側で自動探索を先に実行する。
      setData(await runSearchAgent(projectId));
      onChanged?.();
    } catch (e) {
      setAgentError(String(e));
    } finally {
      setAgentBusy(false);
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

      {/* 🏆 営業推奨連絡先（最上部）。発見メールを営業のしやすさ順に格付け。 */}
      {data?.sales_contacts && data.sales_contacts.length > 0 && (
        <div className="mt-3">
          <SalesRankingSection
            projectId={projectId}
            contacts={data.sales_contacts}
            onApply={onApply}
          />
        </div>
      )}

      {/* 🔎 じっくり調査（推奨・非同期ジョブ）。重い探索をまとめて実行し進捗表示。 */}
      <div className="mt-3">
        <DeepInvestigationSection
          projectId={projectId}
          onDone={refetchDiscovery}
        />
      </div>

      {/* Contact Hunter（誰に送るか）＋ AI連絡先リサーチ ＋ AI Web調査。
          単発実行は残す（推奨は上の「じっくり調査」）。 */}
      <div className="mt-3 space-y-3">
        <ContactHunterSection projectId={projectId} onChanged={onChanged} />
        <AiResearchSection
          projectId={projectId}
          data={data}
          busy={aiBusy}
          error={aiError}
          onRun={onRunAi}
          onApply={onApply}
        />
        {/* AI Web調査（検索エンジン＋公式サイト横断クロール。自動抽出/AI調査と区別） */}
        <WebResearchSection
          projectId={projectId}
          data={data}
          busy={webBusy}
          error={webError}
          onRun={onRunWeb}
          onApply={onApply}
        />
        {/* AI Document Reader（ページ全体を読解。自動抽出/AI調査/Web調査/担当者と区別） */}
        <DocumentReaderSection
          projectId={projectId}
          data={data}
          busy={docBusy}
          error={docError}
          onRun={onRunDoc}
          onApply={onApply}
        />
        {/* AI Search Agent（次に見るページを判断しながら反復探索。他レイヤーと区別） */}
        <SearchAgentSection
          projectId={projectId}
          data={data}
          busy={agentBusy}
          error={agentError}
          onRun={onRunAgent}
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

          {/* 公式サイト（実際の企業ドメイン。クラファン/プロフィールURLは表示しない）。
              取得できない場合は「公式サイト未発見」と表示する。 */}
          <div className="text-xs">
            <span className="font-semibold text-slate-500">公式サイト：</span>{" "}
            {data.official_site_url ? (
              <a
                href={data.official_site_url}
                target="_blank"
                rel="noreferrer"
                className="break-all text-blue-700 hover:underline"
              >
                {data.official_site_url.replace(/^https?:\/\//, "")}
              </a>
            ) : (
              <span className="text-slate-400">公式サイト未発見</span>
            )}
          </div>

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
