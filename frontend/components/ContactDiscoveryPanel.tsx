"use client";

import { useEffect, useState } from "react";

import {
  applyDiscoveryToCrm,
  type ContactDiscovery,
  fetchContactDiscovery,
  fetchOutreachMessage,
  formatDateTime,
  type OutreachMessage,
  runContactDiscovery,
} from "@/lib/api";

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
const QUICK_LINKS: {
  key: keyof ContactDiscovery;
  label: string;
  channel: string;
}[] = [
  { key: "primary_contact_form_url", label: "問い合わせフォーム", channel: "contact_form" },
  { key: "instagram_url", label: "Instagram", channel: "instagram" },
  { key: "linkedin_url", label: "LinkedIn", channel: "linkedin" },
  { key: "facebook_url", label: "Facebook", channel: "facebook" },
  { key: "twitter_url", label: "X / Twitter", channel: "twitter" },
  { key: "youtube_url", label: "YouTube", channel: "youtube" },
  { key: "official_site_url", label: "公式サイト", channel: "official_site" },
];

// 発見済みの外部連絡先リンクへ新しいタブで飛べるクイックボタン群。
// URL が存在するチャネルだけ表示し、recommended_channel と一致するものは強調する。
function QuickContactLinks({ data }: { data: ContactDiscovery }) {
  const available = QUICK_LINKS.filter((l) => {
    const url = data[l.key];
    return typeof url === "string" && url.length > 0;
  });

  if (available.length === 0) {
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
        {available.map((l) => {
          const recommended = data.recommended_channel === l.channel;
          return (
            <a
              key={l.key as string}
              href={data[l.key] as string}
              target="_blank"
              rel="noopener noreferrer"
              className={
                recommended
                  ? "inline-flex items-center gap-1 rounded-md border border-emerald-400 bg-emerald-50 px-3 py-1.5 text-xs font-semibold text-emerald-700 shadow-sm hover:bg-emerald-100"
                  : "inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 hover:bg-slate-50"
              }
            >
              {recommended ? `★ おすすめ：${l.label}` : `${l.label}を開く`}
              <span aria-hidden>↗</span>
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

// メールアドレスが見つからない案件向けの短文アウトリーチ文（フォーム / SNS DM 用）。
// 既存の営業メールとは別に、貼り付けてすぐ使える短い営業文を生成する。
function ShortOutreach({
  projectId,
  channel,
}: {
  projectId: number;
  channel: string;
}) {
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

  const channelLabel = CHANNEL_LABELS[channel] ?? channel;

  return (
    <div className="rounded-md border border-violet-200 bg-violet-50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs font-semibold text-violet-800">
          短文アウトリーチ文（{channelLabel}用・メール不要）
        </p>
        <button
          onClick={generate}
          disabled={busy}
          className="rounded border border-violet-300 bg-white px-2 py-1 text-xs font-medium text-violet-700 hover:bg-violet-100 disabled:opacity-50"
        >
          {busy ? "生成中…" : msg ? "再生成" : "短文を生成"}
        </button>
      </div>
      <p className="mt-1 text-xs text-violet-600">
        メールアドレスが無くても、問い合わせフォームやSNSのDMにそのまま貼り付けられる
        短い営業文（約300〜600文字）を作成します。送信は手動で行ってください。
      </p>

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {msg && (
        <div className="mt-2 rounded border border-violet-200 bg-white p-3">
          <pre className="whitespace-pre-wrap font-sans text-sm text-slate-800">
            {msg.text}
          </pre>
          <div className="mt-2 flex flex-wrap items-center gap-2">
            <CopyButton text={msg.text} label="アウトリーチ文をコピー" />
            <span className="text-xs text-slate-400">{msg.char_count}文字</span>
          </div>
        </div>
      )}
    </div>
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

          {/* 短文アウトリーチ文（メールが無く、推奨チャネルがフォーム/SNSのとき） */}
          {data.recommended_channel &&
            SHORT_OUTREACH_CHANNELS.includes(data.recommended_channel) && (
              <ShortOutreach
                projectId={projectId}
                channel={data.recommended_channel}
              />
            )}

          {/* 外部連絡先へのクイックリンク（短文を生成→コピー→そのまま開いて貼り付け） */}
          <QuickContactLinks data={data} />

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
