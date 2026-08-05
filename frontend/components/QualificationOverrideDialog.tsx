"use client";

import { useEffect, useRef, useState } from "react";

import {
  QUALIFICATION_LABELS,
  STAGE_LABELS,
  type OverridePayload,
  type QualificationDecision,
  type QualificationStage,
} from "@/lib/api";

const DECISIONS: QualificationDecision[] = ["blocked", "review", "clear"];

/** http(s) だけを受け付ける（db:// / file:// / ftp:// / ローカルパスは拒否）。 */
export function isAllowedEvidenceUrl(value: string): boolean {
  const raw = (value ?? "").trim();
  if (!raw) return false;
  try {
    const u = new URL(raw);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

type Props = {
  open: boolean;
  projectTitle?: string;
  stage: QualificationStage;
  /** 機械判定。初期値の参考として表示する（自動では選ばない）。 */
  machineDecision?: QualificationDecision | null;
  busy?: boolean;
  /** API から返ったエラー（422 等）。フロント検証とは別に表示する。 */
  apiError?: string | null;
  onSubmit: (payload: OverridePayload) => void;
  onCancel: () => void;
};

/**
 * 人が営業対象判定を上書きするダイアログ。
 *
 * - 理由と根拠 URL は**どちらも必須**（根拠 URL は http(s) のみ）
 * - 機械判定は削除せず、人の判断を**新しい履歴**として追加する
 * - 自動アーカイブ・メール送信は行わない
 * - AI が自動で実行することはない（この画面からの明示操作だけ）
 */
export default function QualificationOverrideDialog({
  open,
  projectTitle,
  stage,
  machineDecision,
  busy = false,
  apiError,
  onSubmit,
  onCancel,
}: Props) {
  const [decision, setDecision] = useState<QualificationDecision | "">("");
  const [reason, setReason] = useState("");
  const [evidenceUrl, setEvidenceUrl] = useState("");
  const [touched, setTouched] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);
  const firstFieldRef = useRef<HTMLSelectElement>(null);

  useEffect(() => {
    if (open) {
      setDecision("");
      setReason("");
      setEvidenceUrl("");
      setTouched(false);
      // 開いたら最初の入力へフォーカスを移す。
      window.setTimeout(() => firstFieldRef.current?.focus(), 0);
    }
  }, [open, stage]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !busy) onCancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onCancel]);

  if (!open) return null;

  const reasonOk = reason.trim().length > 0;
  const urlOk = isAllowedEvidenceUrl(evidenceUrl);
  const decisionOk = decision !== "";
  const canSubmit = reasonOk && urlOk && decisionOk && !busy;

  function submit() {
    setTouched(true);
    // canSubmit は decisionOk（decision !== ""）を含むため、これで decision は
    // QualificationDecision に絞り込まれる。
    if (!canSubmit) return;
    onSubmit({
      stage,
      decision,
      reason: reason.trim(),
      evidence_url: evidenceUrl.trim(),
    });
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="qualification-override-title"
    >
      <div
        ref={dialogRef}
        className="w-full max-w-lg rounded-lg bg-white p-5 shadow-xl"
      >
        <h2
          id="qualification-override-title"
          className="text-base font-semibold text-slate-800"
        >
          営業対象判定を人の判断で上書き
        </h2>
        <p className="mt-1 text-xs text-slate-500">
          {projectTitle ? `${projectTitle} / ` : ""}
          対象ステージ: {STAGE_LABELS[stage]}
          {machineDecision
            ? ` / 機械判定: ${QUALIFICATION_LABELS[machineDecision]}`
            : ""}
        </p>

        <div className="mt-4 space-y-3">
          <div>
            <label
              htmlFor="override-decision"
              className="block text-xs font-medium text-slate-700"
            >
              上書き後の判定 <span className="text-rose-600">必須</span>
            </label>
            <select
              id="override-decision"
              ref={firstFieldRef}
              value={decision}
              onChange={(e) =>
                setDecision(e.target.value as QualificationDecision | "")
              }
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            >
              <option value="">選択してください</option>
              {DECISIONS.map((d) => (
                <option key={d} value={d}>
                  {QUALIFICATION_LABELS[d]}
                </option>
              ))}
            </select>
            {touched && !decisionOk && (
              <p className="mt-1 text-xs text-rose-600">判定を選択してください。</p>
            )}
          </div>

          <div>
            <label
              htmlFor="override-reason"
              className="block text-xs font-medium text-slate-700"
            >
              判断した理由 <span className="text-rose-600">必須</span>
            </label>
            <textarea
              id="override-reason"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              rows={3}
              maxLength={1000}
              placeholder="例: 公式サイトの会社概要でメーカー本人と確認した"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
            />
            {touched && !reasonOk && (
              <p className="mt-1 text-xs text-rose-600">
                理由は必須です（空白のみは不可）。
              </p>
            )}
          </div>

          <div>
            <label
              htmlFor="override-evidence-url"
              className="block text-xs font-medium text-slate-700"
            >
              根拠 URL <span className="text-rose-600">必須</span>
            </label>
            <input
              id="override-evidence-url"
              type="url"
              inputMode="url"
              value={evidenceUrl}
              onChange={(e) => setEvidenceUrl(e.target.value)}
              placeholder="https://example.com/about"
              className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm"
              aria-describedby="override-url-help"
            />
            <p id="override-url-help" className="mt-1 text-[11px] text-slate-500">
              http:// または https:// のみ。内部参照（db://）やローカルファイルは
              根拠にできません。
            </p>
            {touched && !urlOk && (
              <p className="mt-1 text-xs text-rose-600">
                http:// または https:// で始まる URL を入力してください。
              </p>
            )}
          </div>
        </div>

        <div className="mt-4 rounded border border-slate-200 bg-slate-50 p-3 text-[11px] leading-relaxed text-slate-600">
          <p>
            この操作は機械判定を削除せず、人の判断を新しい履歴として追加します。
          </p>
          <p>自動アーカイブやメール送信は行いません。</p>
        </div>

        {apiError && (
          <p role="alert" className="mt-3 text-xs text-rose-600">
            {apiError}
          </p>
        )}

        <div className="mt-4 flex justify-end gap-2">
          <button
            type="button"
            onClick={onCancel}
            disabled={busy}
            className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-50 disabled:opacity-50"
          >
            キャンセル
          </button>
          <button
            type="button"
            onClick={submit}
            disabled={!canSubmit}
            title={
              canSubmit
                ? "人の判断を履歴に追加します"
                : "判定・理由・根拠URL（http(s)）をすべて入力してください"
            }
            className="rounded bg-blue-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-blue-500 disabled:opacity-50"
          >
            {busy ? "登録中…" : "上書きを登録"}
          </button>
        </div>
      </div>
    </div>
  );
}
