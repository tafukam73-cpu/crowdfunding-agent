"use client";

import { useCallback, useEffect, useState } from "react";

import QualificationBadge from "@/components/QualificationBadge";
import QualificationOverrideDialog from "@/components/QualificationOverrideDialog";
import {
  CONFIDENCE_HELP,
  CONFIDENCE_LABELS,
  INTERNAL_EVIDENCE_LABEL,
  QUALIFICATION_LABELS,
  STAGE_LABELS,
  fetchLeadQualification,
  overrideLeadQualification,
  recheckLeadQualification,
  type LeadQualificationResult,
  type OverridePayload,
  type QualificationEvidence,
  type QualificationFinding,
  type QualificationStage,
} from "@/lib/api";

const STAGES: QualificationStage[] = ["pre_research", "pre_outreach"];
const EXCERPT_LIMIT = 120;

function fmt(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("ja-JP");
}

/**
 * 証跡 1 件。
 *
 * `is_external_link=false`（内部 DB 参照）は **リンク化せず、db:// も画面に出さない**。
 * 外部リンクは新しいタブで開くことを文言でも示す。HTML は挿入しない
 * （dangerouslySetInnerHTML は使わない）。
 */
function EvidenceRow({ ev }: { ev: QualificationEvidence }) {
  const [open, setOpen] = useState(false);
  const excerpt = ev.excerpt ?? "";
  const long = excerpt.length > EXCERPT_LIMIT;
  const shown = long && !open ? `${excerpt.slice(0, EXCERPT_LIMIT)}…` : excerpt;

  return (
    <li className="rounded border border-slate-200 bg-white p-2 text-[11px]">
      {ev.claim && <p className="font-medium text-slate-700">{ev.claim}</p>}
      <p className="mt-0.5 text-slate-500">
        {ev.is_external_link && ev.source_url ? (
          <a
            href={ev.source_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-blue-600 underline break-all"
          >
            {ev.source_url}
            <span className="ml-1 text-[10px] text-slate-400">
              （新しいタブで開く）
            </span>
          </a>
        ) : (
          <span className="text-slate-500">{INTERNAL_EVIDENCE_LABEL}</span>
        )}
      </p>
      <p className="mt-0.5 text-slate-400">
        {[ev.source_kind, ev.method, ev.checked_at ? fmt(ev.checked_at) : null]
          .filter(Boolean)
          .join(" / ") || "—"}
      </p>
      {excerpt && (
        <p className="mt-0.5 whitespace-pre-wrap break-words text-slate-500">
          {shown}
          {long && (
            <button
              type="button"
              onClick={() => setOpen((v) => !v)}
              className="ml-1 text-blue-600 underline"
            >
              {open ? "折りたたむ" : "全文を表示"}
            </button>
          )}
        </p>
      )}
    </li>
  );
}

function severityStyle(severity: string): string {
  if (severity === "blocker") return "border-rose-300 bg-rose-50";
  if (severity === "review") return "border-amber-300 bg-amber-50";
  return "border-slate-200 bg-white";
}

function severityLabel(severity: string): string {
  if (severity === "blocker") return "停止";
  if (severity === "review") return "要確認";
  return "情報";
}

function FindingCard({ f }: { f: QualificationFinding }) {
  const [open, setOpen] = useState(f.severity !== "info");
  return (
    <li className={`rounded border p-2 ${severityStyle(f.severity)}`}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-start justify-between gap-2 text-left"
      >
        <span className="text-xs font-medium text-slate-800">
          <span className="mr-1 text-slate-500">{f.code}.</span>
          {f.label}
          <span className="ml-2 rounded bg-white/70 px-1.5 py-0.5 text-[10px] text-slate-600">
            {severityLabel(f.severity)}
          </span>
          <span
            className="ml-1 rounded bg-white/70 px-1.5 py-0.5 text-[10px] text-slate-600"
            title={CONFIDENCE_HELP}
          >
            証跡 {CONFIDENCE_LABELS[f.confidence]}
          </span>
        </span>
        <span className="shrink-0 text-[11px] text-slate-500">
          {open ? "閉じる" : "詳細"}
        </span>
      </button>
      {open && (
        <div className="mt-1.5 space-y-1.5">
          <p className="text-[11px] leading-relaxed text-slate-600">{f.reason}</p>
          {f.downgrade_reason && (
            <p className="text-[11px] text-slate-500">
              降格理由: {f.downgrade_reason}
            </p>
          )}
          {f.evidence.length > 0 ? (
            <ul className="space-y-1">
              {f.evidence.map((ev, i) => (
                <EvidenceRow key={i} ev={ev} />
              ))}
            </ul>
          ) : (
            <p className="text-[11px] text-slate-400">証跡なし</p>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * 営業対象判定（Lead Qualification Engine）パネル。
 *
 * - 初期ステージは「調査前（pre_research）」。送信準備前は明示的に切り替える
 * - 画面表示だけでは判定を実行しない（GET は履歴を増やさない）
 * - 再判定・上書きは人の明示操作のみ。自動アーカイブは行わない
 * - 数値スコア・確率・返信率・成功率は表示しない
 */
export default function LeadQualificationPanel({
  projectId,
  projectTitle,
  onSnapshotChange,
}: {
  projectId: number;
  projectTitle?: string;
  /** pre_research の判定が変わったとき（一覧スナップショット更新の通知用）。 */
  onSnapshotChange?: () => void;
}) {
  const [stage, setStage] = useState<QualificationStage>("pre_research");
  const [data, setData] = useState<LeadQualificationResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [dialogError, setDialogError] = useState<string | null>(null);

  const load = useCallback(
    async (s: QualificationStage) => {
      setLoading(true);
      setError(null);
      try {
        setData(await fetchLeadQualification(projectId, s));
      } catch (e) {
        setError(e instanceof Error ? e.message : "判定を取得できませんでした");
        setData(null);
      } finally {
        setLoading(false);
      }
    },
    [projectId]
  );

  useEffect(() => {
    void load(stage);
  }, [load, stage]);

  async function onRecheck() {
    const ok = window.confirm(
      `${STAGE_LABELS[stage]}の判定をやり直します。\n` +
        "判定履歴に新しい1件が追加されます。よろしいですか？"
    );
    if (!ok) return;
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const res = await recheckLeadQualification(projectId, stage);
      setData(res.qualification);
      setNotice(
        res.snapshot_updated
          ? "再判定しました。一覧の判定も更新されます。"
          : "再判定しました。送信準備前の判定は一覧には反映されません。"
      );
      if (res.snapshot_updated) onSnapshotChange?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "再判定に失敗しました");
    } finally {
      setBusy(false);
    }
  }

  async function onOverride(payload: OverridePayload) {
    setBusy(true);
    setDialogError(null);
    try {
      const res = await overrideLeadQualification(projectId, payload);
      setData(res.qualification);
      setDialogOpen(false);
      setNotice(
        res.changed
          ? "人の判断で上書きしました（履歴に追加）。"
          : "判定値は同じですが、監査履歴を追加しました。"
      );
      if (payload.stage === "pre_research") onSnapshotChange?.();
    } catch (e) {
      setDialogError(e instanceof Error ? e.message : "上書きに失敗しました");
    } finally {
      setBusy(false);
    }
  }

  const blockers = data?.findings.filter((f) => f.severity === "blocker") ?? [];
  const reviews = data?.findings.filter((f) => f.severity === "review") ?? [];
  const infos = data?.findings.filter((f) => f.severity === "info") ?? [];

  return (
    <section id="lead-qualification" className="rounded-lg border border-slate-200 bg-slate-50 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-sm font-semibold text-slate-800">営業対象判定</h2>
        <div className="flex flex-wrap items-center gap-2">
          <div role="tablist" aria-label="判定ステージ" className="flex gap-1">
            {STAGES.map((s) => (
              <button
                key={s}
                role="tab"
                aria-selected={stage === s}
                onClick={() => setStage(s)}
                className={`rounded px-2.5 py-1 text-xs font-medium ${
                  stage === s
                    ? "bg-slate-800 text-white"
                    : "border border-slate-300 bg-white text-slate-600 hover:bg-slate-100"
                }`}
              >
                {STAGE_LABELS[s]}
              </button>
            ))}
          </div>
          <button
            type="button"
            onClick={onRecheck}
            disabled={busy || loading}
            title="判定履歴に新しい1件を追加して再判定します"
            className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-50"
          >
            {busy ? "実行中…" : "再判定"}
          </button>
          <button
            type="button"
            onClick={() => {
              setDialogError(null);
              setDialogOpen(true);
            }}
            disabled={busy || loading}
            title="人の判断で判定を上書きします（理由と根拠URLが必要）"
            className="rounded border border-slate-300 bg-white px-2.5 py-1 text-xs text-slate-700 hover:bg-slate-100 disabled:opacity-50"
          >
            人の判断で上書き
          </button>
        </div>
      </div>

      <p className="mt-1 text-[11px] text-slate-500">
        {stage === "pre_research"
          ? "調査（連絡先探索）に進めてよいかの判定です。一覧の判定列はこちらを表示します。"
          : "Gmail 下書きを作る前の判定です。一覧の判定列には反映されません。"}
      </p>

      {loading && <p className="mt-3 text-xs text-slate-500">読み込み中…</p>}
      {error && (
        <p role="alert" className="mt-3 text-xs text-rose-600">
          {error}
        </p>
      )}
      {notice && (
        <p role="status" className="mt-3 text-xs text-emerald-700">
          {notice}
        </p>
      )}

      {data && !loading && (
        <>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <QualificationBadge
              decision={data.effective_decision}
              title={`${STAGE_LABELS[data.stage]}の判定: ${
                QUALIFICATION_LABELS[data.effective_decision]
              }`}
            />
            <span className="rounded bg-white px-2 py-0.5 text-[11px] text-slate-600">
              {data.persisted
                ? "保存済み判定"
                : "暫定判定（まだ履歴に保存されていません）"}
            </span>
            {data.overridden && (
              <span className="rounded bg-indigo-100 px-2 py-0.5 text-[11px] text-indigo-800">
                人の判断で上書き済み
              </span>
            )}
            <span className="text-[11px] text-slate-500">
              判定日時: {fmt(data.evaluated_at)}
            </span>
          </div>

          {data.machine_decision !== data.effective_decision && (
            <p className="mt-2 rounded border border-indigo-200 bg-indigo-50 p-2 text-[11px] text-indigo-900">
              機械判定は「{QUALIFICATION_LABELS[data.machine_decision]}」ですが、
              人の判断で「{QUALIFICATION_LABELS[data.effective_decision]}
              」として扱われています。
              {data.override_reason && <> 理由: {data.override_reason}</>}
            </p>
          )}

          <dl className="mt-3 grid grid-cols-2 gap-2 text-[11px] sm:grid-cols-4">
            <div className="rounded bg-white p-2">
              <dt className="text-slate-500">停止（blocker）</dt>
              <dd className="font-medium text-slate-800">
                {data.blocker_codes.length ? data.blocker_codes.join(", ") : "なし"}
              </dd>
            </div>
            <div className="rounded bg-white p-2">
              <dt className="text-slate-500">要確認（review）</dt>
              <dd className="font-medium text-slate-800">
                {data.review_codes.length ? data.review_codes.join(", ") : "なし"}
              </dd>
            </div>
            <div className="rounded bg-white p-2">
              <dt className="text-slate-500">証跡の数</dt>
              <dd className="font-medium text-slate-800">{data.evidence_count}</dd>
            </div>
            <div className="rounded bg-white p-2">
              <dt className="text-slate-500">判定ルール</dt>
              <dd className="font-medium text-slate-800">{data.rule_version}</dd>
            </div>
          </dl>

          {data.positive_facts.length > 0 && (
            <div className="mt-3">
              <h3 className="text-xs font-semibold text-slate-700">
                営業する根拠（確認できた事実）
              </h3>
              <ul className="mt-1 space-y-1">
                {data.positive_facts.map((p) => (
                  <li
                    key={p.key}
                    className="rounded border border-emerald-200 bg-emerald-50 p-2"
                  >
                    <p className="text-[11px] font-medium text-emerald-900">
                      {p.label}
                    </p>
                    {p.evidence.length > 0 && (
                      <ul className="mt-1 space-y-1">
                        {p.evidence.map((ev, i) => (
                          <EvidenceRow key={i} ev={ev} />
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {[
            { title: "停止している項目", items: blockers },
            { title: "要確認の項目", items: reviews },
            { title: "確認した項目（該当なしを含む）", items: infos },
          ].map(
            (group) =>
              group.items.length > 0 && (
                <div key={group.title} className="mt-3">
                  <h3 className="text-xs font-semibold text-slate-700">
                    {group.title}（{group.items.length}）
                  </h3>
                  <ul className="mt-1 space-y-1">
                    {group.items.map((f) => (
                      <FindingCard key={f.code} f={f} />
                    ))}
                  </ul>
                </div>
              )
          )}
        </>
      )}

      <QualificationOverrideDialog
        open={dialogOpen}
        projectTitle={projectTitle}
        stage={stage}
        machineDecision={data?.machine_decision ?? null}
        busy={busy}
        apiError={dialogError}
        onSubmit={onOverride}
        onCancel={() => setDialogOpen(false)}
      />
    </section>
  );
}
