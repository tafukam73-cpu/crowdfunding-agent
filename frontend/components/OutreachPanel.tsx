"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  fetchOutreach,
  generateOutreach,
  getContactIntelligenceJob,
  OUTREACH_LANGUAGE_LABELS,
  OUTREACH_STATUS_LABELS,
  type Outreach,
  type OutreachStatus,
} from "@/lib/api";

const LANG_ORDER = ["en", "ko", "zh", "ja"];

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

// 営業実行パイプライン：営業メール生成（4 言語）・下書き・営業状況・Gmail 送信。
// 生成は背景ジョブ（Claude を含みうる）で、ボタン押下時のみ起動する（GET では起動しない）。
export default function OutreachPanel({ projectId }: { projectId: number }) {
  const [outreach, setOutreach] = useState<Outreach | null>(null);
  const [loading, setLoading] = useState(true);
  const [generating, setGenerating] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [step, setStep] = useState<string | null>(null);
  const [lang, setLang] = useState<string>("en");
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

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
  const langs = variants
    ? LANG_ORDER.filter((l) => variants[l])
    : [];
  const current = variants?.[lang] ?? null;
  const hasDraft = !!outreach?.generated_at;

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

      {/* Open Draft：言語切り替え＋下書き＋Gmail 送信 */}
      {hasDraft && current && (
        <div className="rounded-md border border-slate-200 bg-white p-3">
          <div className="flex flex-wrap items-center gap-1">
            {langs.map((l) => (
              <button
                key={l}
                onClick={() => setLang(l)}
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
              onClick={() =>
                navigator.clipboard?.writeText(
                  `${current.subject}\n\n${current.body}`
                )
              }
              className="rounded border border-slate-300 px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
            >
              コピー
            </button>
            {outreach?.gmail_compose_url && lang === outreach.generated_language && (
              <a
                href={outreach.gmail_compose_url}
                target="_blank"
                rel="noopener noreferrer"
                className="rounded bg-red-600 px-3 py-1 text-xs font-medium text-white hover:bg-red-500"
              >
                Gmail で開く ↗
              </a>
            )}
          </div>
          {outreach?.notes && (
            <p className="mt-2 text-[11px] text-slate-400">{outreach.notes}</p>
          )}
        </div>
      )}
    </div>
  );
}
