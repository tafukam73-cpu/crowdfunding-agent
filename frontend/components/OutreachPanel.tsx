"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  confirmOutreachReply,
  fetchOutreach,
  generateOutreach,
  generateOutreachFollowup,
  getContactIntelligenceJob,
  markOutreachSent,
  OUTREACH_LANGUAGE_LABELS,
  OUTREACH_STATUS_LABELS,
  previewOutreachReply,
  type Outreach,
  type OutreachStatus,
  type ReplyAnalysis,
  REPLY_INTENT_LABELS,
  saveOutreachDraft,
} from "@/lib/api";

const LANG_ORDER = ["en", "ko", "zh", "ja"];

function fmt(dt: string | null): string {
  if (!dt) return "—";
  try {
    return new Date(dt).toLocaleString("ja-JP", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return dt;
  }
}

function StatusBadge({ status }: { status: OutreachStatus }) {
  const color =
    status === "contract"
      ? "bg-emerald-100 text-emerald-700"
      : status === "lost"
        ? "bg-slate-100 text-slate-500"
        : status === "replied" || status === "negotiating"
          ? "bg-amber-100 text-amber-700"
          : status === "sent" || status === "opened"
            ? "bg-blue-100 text-blue-700"
            : "bg-indigo-100 text-indigo-700";
  return (
    <span className={`rounded px-1.5 py-0.5 text-[11px] font-medium ${color}`}>
      {OUTREACH_STATUS_LABELS[status]}
    </span>
  );
}

// 営業実行パイプライン：営業メール生成（4 言語）・下書き編集・送信記録・
// フォローアップ・返信登録。生成/フォローは背景ジョブ、編集保存は同期。
// Gmail で開いただけでは送信済みにしない（「送信済みとして記録」ボタンのみ）。
export default function OutreachPanel({ projectId }: { projectId: number }) {
  const [outreach, setOutreach] = useState<Outreach | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [lang, setLang] = useState<string>("en");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // 編集モード
  const [editing, setEditing] = useState(false);
  const [editSubject, setEditSubject] = useState("");
  const [editBody, setEditBody] = useState("");
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const o = await fetchOutreach(projectId);
      setOutreach(o);
      if (o?.generated_language) setLang(o.generated_language);
    } catch {
      setError("営業アウトリーチを取得できませんでした");
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, [load]);

  const pollJob = useCallback(
    (jobId: number) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const job = await getContactIntelligenceJob(jobId);
          setStep(job.current_step ?? null);
          if (
            job.status === "completed" ||
            job.status === "failed" ||
            job.status === "cancelled"
          ) {
            if (pollRef.current) clearInterval(pollRef.current);
            setGenerating(false);
            setStep(null);
            if (job.status === "failed") {
              setError(job.error || "生成に失敗しました");
            }
            await load();
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
          setGenerating(false);
        }
      }, 1500);
    },
    [load]
  );

  const onGenerate = useCallback(async () => {
    setError(null);
    setGenerating(true);
    setStep("生成ジョブを起動中…");
    try {
      const res = await generateOutreach(projectId);
      if (res.outreach) setOutreach(res.outreach);
      if (res.job_status === "completed") {
        setGenerating(false);
        setStep(null);
        await load();
      } else {
        pollJob(res.job_id);
      }
    } catch (e) {
      setGenerating(false);
      setStep(null);
      setError(e instanceof Error ? e.message : "生成に失敗しました");
    }
  }, [projectId, load, pollJob]);

  const variants = outreach?.generated_variants ?? null;
  const langs = variants ? LANG_ORDER.filter((l) => variants[l]) : [];
  const current = variants?.[lang] ?? null;
  const hasDraft = !!outreach?.generated_at;
  const isSent = !!outreach?.sent_at;
  const isReplied =
    outreach?.outreach_status === "replied" ||
    outreach?.outreach_status === "negotiating";
  const isTerminal =
    outreach?.outreach_status === "contract" ||
    outreach?.outreach_status === "lost";

  // --- 編集 ---
  function startEdit() {
    if (!current) return;
    setEditSubject(current.subject ?? "");
    setEditBody(current.body ?? "");
    setEditing(true);
  }
  async function saveEdit() {
    setSaving(true);
    setError(null);
    try {
      const o = await saveOutreachDraft(projectId, {
        subject: editSubject,
        body: editBody,
        language: lang,
      });
      setOutreach(o);
      setEditing(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存に失敗しました");
    } finally {
      setSaving(false);
    }
  }

  // --- 送信済みとして記録 ---
  const [marking, setMarking] = useState(false);
  async function onMarkSent() {
    if (
      !window.confirm(
        "この内容を送信済みとして記録します。\n（実際のメール送信は Gmail 側で行ってください）"
      )
    )
      return;
    setMarking(true);
    setError(null);
    try {
      const res = await markOutreachSent(projectId, { language: lang });
      setOutreach(res.outreach);
    } catch (e) {
      setError(e instanceof Error ? e.message : "送信記録に失敗しました");
    } finally {
      setMarking(false);
    }
  }

  // --- フォローアップ生成 ---
  async function onFollowup() {
    setError(null);
    setGenerating(true);
    setStep("フォローアップ生成ジョブを起動中…");
    try {
      const res = await generateOutreachFollowup(projectId);
      if (!res.eligible) {
        setGenerating(false);
        setStep(null);
        setError(res.reason || "フォロー対象外です");
        return;
      }
      if (res.outreach) setOutreach(res.outreach);
      if (res.job_status === "completed" || res.job_id == null) {
        setGenerating(false);
        setStep(null);
        await load();
      } else {
        pollJob(res.job_id);
      }
    } catch (e) {
      setGenerating(false);
      setStep(null);
      setError(e instanceof Error ? e.message : "フォロー生成に失敗しました");
    }
  }

  return (
    <div className="space-y-3">
      {/* 営業状況ヘッダー */}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="text-slate-500">営業状況:</span>
          {outreach ? (
            <StatusBadge status={outreach.outreach_status} />
          ) : (
            <span className="text-slate-400">未生成</span>
          )}
          {typeof outreach?.priority_score === "number" && (
            <span className="text-xs text-slate-400">
              優先度 {outreach.priority_score}
            </span>
          )}
          {outreach?.user_edited && (
            <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] text-slate-500">
              編集済み
            </span>
          )}
        </div>
        <button
          onClick={onGenerate}
          disabled={generating}
          className="rounded bg-indigo-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {generating
            ? "生成中…"
            : hasDraft
              ? "営業メールを再生成"
              : "Generate Email（4言語）"}
        </button>
      </div>

      {step && <p className="text-xs text-indigo-600">{step}</p>}
      {error && <p className="text-xs text-red-600">{error}</p>}

      {loading && <p className="text-sm text-slate-400">読み込み中…</p>}

      {!loading && !outreach && !generating && (
        <p className="text-sm text-slate-500">
          まだ営業メールを生成していません。「Generate Email」で英語・韓国語・中国語・
          日本語の下書きを作成します（生成は背景ジョブで実行されます）。
        </p>
      )}

      {/* Open Draft：言語切り替え＋下書き（編集可）＋Gmail 送信＋送信記録 */}
      {hasDraft && current && (
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center gap-1">
            {langs.map((l) => (
              <button
                key={l}
                onClick={() => {
                  setLang(l);
                  setEditing(false);
                }}
                className={`rounded px-2 py-1 text-xs ${
                  l === lang
                    ? "bg-indigo-600 text-white"
                    : "bg-slate-100 text-slate-600 hover:bg-slate-200"
                }`}
              >
                {OUTREACH_LANGUAGE_LABELS[l] ?? l}
                {l === outreach?.generated_language && " ★"}
              </button>
            ))}
            {outreach?.recipient && (
              <span className="ml-auto text-xs text-slate-400">
                宛先: {outreach.recipient}
              </span>
            )}
          </div>

          {editing ? (
            <div className="mt-2 space-y-2">
              <div>
                <p className="text-[11px] font-medium text-slate-400">件名</p>
                <input
                  value={editSubject}
                  onChange={(e) => setEditSubject(e.target.value)}
                  className="w-full rounded border border-slate-300 px-2 py-1 text-sm"
                />
              </div>
              <div>
                <p className="text-[11px] font-medium text-slate-400">本文</p>
                <textarea
                  value={editBody}
                  onChange={(e) => setEditBody(e.target.value)}
                  rows={10}
                  className="w-full rounded border border-slate-300 px-2 py-1 text-xs"
                />
              </div>
              <div className="flex gap-2">
                <button
                  onClick={saveEdit}
                  disabled={saving}
                  className="rounded bg-emerald-600 px-3 py-1 text-xs font-medium text-white hover:bg-emerald-500 disabled:opacity-50"
                >
                  {saving ? "保存中…" : "保存"}
                </button>
                <button
                  onClick={() => setEditing(false)}
                  className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  キャンセル
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="mt-2">
                <p className="text-[11px] font-medium text-slate-400">件名</p>
                <p className="text-sm font-medium text-slate-800">
                  {current.subject}
                </p>
              </div>
              <div className="mt-2">
                <p className="text-[11px] font-medium text-slate-400">本文</p>
                <pre className="mt-0.5 max-h-72 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-xs text-slate-700">
                  {current.body}
                </pre>
              </div>

              <div className="mt-2 flex flex-wrap items-center gap-2">
                <button
                  onClick={startEdit}
                  className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  編集
                </button>
                <button
                  onClick={() =>
                    navigator.clipboard?.writeText(
                      `${current.subject}\n\n${current.body}`
                    )
                  }
                  className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
                >
                  コピー
                </button>
                {outreach?.gmail_compose_url &&
                  lang === outreach.generated_language && (
                    <a
                      href={outreach.gmail_compose_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-500"
                    >
                      Gmail で開く ↗
                    </a>
                  )}
                {!isSent && !isTerminal && (
                  <button
                    onClick={onMarkSent}
                    disabled={marking}
                    className="rounded bg-blue-600 px-3 py-1 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
                  >
                    {marking ? "記録中…" : "送信済みとして記録"}
                  </button>
                )}
              </div>
            </>
          )}
          {outreach?.notes && (
            <p className="mt-2 text-[11px] text-slate-400">{outreach.notes}</p>
          )}
        </div>
      )}

      {/* 送信後：スナップショット・フォローアップ・返信登録 */}
      {isSent && outreach && (
        <SentWorkflow
          outreach={outreach}
          onFollowup={onFollowup}
          followBusy={generating}
          canFollow={!isReplied && !isTerminal && outreach.followups_remaining > 0}
          onReplied={(o) => setOutreach(o)}
          projectId={projectId}
        />
      )}
    </div>
  );
}

// ---------------- 送信後ワークフロー（スナップショット・フォロー・返信） ----------------
function SentWorkflow({
  outreach,
  onFollowup,
  followBusy,
  canFollow,
  onReplied,
  projectId,
}: {
  outreach: Outreach;
  onFollowup: () => void;
  followBusy: boolean;
  canFollow: boolean;
  onReplied: (o: Outreach) => void;
  projectId: number;
}) {
  return (
    <div className="rounded-md border border-blue-200 bg-blue-50/40 p-3">
      <p className="text-xs font-bold text-blue-900">📮 送信後ワークフロー</p>

      {/* 送信スナップショット */}
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-[11px] text-slate-600">
        <div>
          <dt className="inline text-slate-400">送信日時: </dt>
          <dd className="inline">{fmt(outreach.sent_at)}</dd>
        </div>
        <div>
          <dt className="inline text-slate-400">送信言語: </dt>
          <dd className="inline">
            {OUTREACH_LANGUAGE_LABELS[outreach.sent_language ?? ""] ??
              outreach.sent_language ??
              "—"}
          </dd>
        </div>
        <div>
          <dt className="inline text-slate-400">宛先: </dt>
          <dd className="inline">{outreach.recipient_email ?? "—"}</dd>
        </div>
        <div>
          <dt className="inline text-slate-400">次フォロー期日: </dt>
          <dd className="inline font-medium text-amber-700">
            {fmt(outreach.followup_due_at)}
          </dd>
        </div>
        <div>
          <dt className="inline text-slate-400">フォロー回数: </dt>
          <dd className="inline">
            {outreach.followup_count} / 2（残り {outreach.followups_remaining}）
          </dd>
        </div>
      </dl>
      {outreach.sent_subject && (
        <p className="mt-1 text-[11px] text-slate-500">
          送信件名スナップショット: 「{outreach.sent_subject}」
        </p>
      )}

      {/* フォローアップ */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {canFollow ? (
          <button
            onClick={onFollowup}
            disabled={followBusy}
            className="rounded bg-amber-600 px-3 py-1 text-xs font-medium text-white hover:bg-amber-500 disabled:opacity-50"
          >
            {followBusy ? "生成中…" : "フォローアップ生成"}
          </button>
        ) : (
          <span className="text-[11px] text-slate-400">
            {outreach.followups_remaining <= 0
              ? "フォロー上限（2回）に到達"
              : "返信あり/商談中のためフォロー対象外"}
          </span>
        )}
      </div>

      {/* 返信登録 */}
      <ReplyRegister
        projectId={projectId}
        outreach={outreach}
        onReplied={onReplied}
      />
    </div>
  );
}

// ---------------- 返信の手動貼り付け（プレビュー → 確定登録） ----------------
function ReplyRegister({
  projectId,
  outreach,
  onReplied,
}: {
  projectId: number;
  outreach: Outreach;
  onReplied: (o: Outreach) => void;
}) {
  const [open, setOpen] = useState(false);
  const [subject, setSubject] = useState("");
  const [from, setFrom] = useState("");
  const [body, setBody] = useState("");
  const [analysis, setAnalysis] = useState<ReplyAnalysis | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function onPreview() {
    if (!body.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      const a = await previewOutreachReply(projectId, {
        incoming_body: body,
        incoming_subject: subject || undefined,
        incoming_from: from || undefined,
      });
      setAnalysis(a);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "解析に失敗しました");
    } finally {
      setBusy(false);
    }
  }

  async function onConfirm() {
    setBusy(true);
    setErr(null);
    try {
      const res = await confirmOutreachReply(projectId, {
        incoming_body: body,
        incoming_subject: subject || undefined,
        incoming_from: from || undefined,
      });
      onReplied(res.outreach);
      setOpen(false);
      setAnalysis(null);
      setBody("");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "登録に失敗しました");
    } finally {
      setBusy(false);
    }
  }

  // 既に返信登録済みならサマリを表示
  if (outreach.reply_intent && !open) {
    return (
      <div className="mt-2 rounded border border-indigo-200 bg-white p-2">
        <p className="text-[11px] font-bold text-indigo-800">
          返信登録済み: {REPLY_INTENT_LABELS[outreach.reply_intent] ?? outreach.reply_intent}
          <span className="ml-1 font-normal text-slate-400">
            （確度 {outreach.reply_confidence}・{fmt(outreach.last_reply_at)}）
          </span>
        </p>
        {outreach.reply_summary && (
          <p className="mt-0.5 text-[11px] text-slate-600">
            {outreach.reply_summary}
          </p>
        )}
        <button
          onClick={() => setOpen(true)}
          className="mt-1 text-[11px] text-indigo-600 hover:underline"
        >
          別の返信を登録する
        </button>
      </div>
    );
  }

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="mt-2 rounded border border-indigo-300 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50"
      >
        返信を登録する
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-2 rounded border border-indigo-200 bg-white p-2">
      <p className="text-[11px] font-bold text-indigo-800">
        受信した返信を貼り付けてください（プレビューは保存されません）
      </p>
      <input
        value={from}
        onChange={(e) => setFrom(e.target.value)}
        placeholder="差出人メール（任意）"
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs"
      />
      <input
        value={subject}
        onChange={(e) => setSubject(e.target.value)}
        placeholder="件名（任意）"
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={5}
        placeholder="返信本文を貼り付け"
        className="w-full rounded border border-slate-300 px-2 py-1 text-xs"
      />
      {err && <p className="text-[11px] text-red-600">{err}</p>}

      {analysis && (
        <div className="rounded bg-indigo-50 p-2 text-[11px] text-slate-700">
          <p>
            <span className="font-bold">意図: </span>
            {REPLY_INTENT_LABELS[analysis.intent] ?? analysis.intent}
            <span className="ml-2 text-slate-400">
              温度感 {analysis.sentiment} / 確度 {analysis.confidence}
            </span>
          </p>
          {analysis.summary && <p className="mt-0.5">{analysis.summary}</p>}
          {analysis.recommended_next_action && (
            <p className="mt-0.5 text-emerald-700">
              推奨: {analysis.recommended_next_action}
            </p>
          )}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        <button
          onClick={onPreview}
          disabled={busy || !body.trim()}
          className="rounded border border-indigo-400 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-50 disabled:opacity-50"
        >
          {busy ? "解析中…" : "解析プレビュー"}
        </button>
        <button
          onClick={onConfirm}
          disabled={busy || !body.trim()}
          className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          返信を記録
        </button>
        <button
          onClick={() => {
            setOpen(false);
            setAnalysis(null);
          }}
          className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
        >
          閉じる
        </button>
      </div>
    </div>
  );
}
