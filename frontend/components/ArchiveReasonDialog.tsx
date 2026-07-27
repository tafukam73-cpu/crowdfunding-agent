"use client";

import { useState } from "react";

import { ARCHIVE_REASONS } from "@/lib/api";

const OTHER = "その他（自由入力）";

type Props = {
  open: boolean;
  // 案件名（単体）。一括のときは count を渡す。
  targetLabel?: string;
  count?: number;
  busy?: boolean;
  onConfirm: (reason: string | undefined) => void;
  onCancel: () => void;
};

/**
 * 営業対象外にするときの確認ダイアログ。
 * 理由は選択式（ARCHIVE_REASONS）を基本とし、「その他」を選ぶと自由入力欄を出す。
 * 未選択でも実行はできる（理由は任意）。一括時は件数を明示する。
 */
export default function ArchiveReasonDialog({
  open,
  targetLabel,
  count,
  busy = false,
  onConfirm,
  onCancel,
}: Props) {
  const [reason, setReason] = useState<string>("");
  const [freeText, setFreeText] = useState<string>("");

  if (!open) return null;

  const isOther = reason === OTHER;
  // 送信する理由：その他なら自由入力の中身、それ以外は選んだラベル。未選択なら undefined。
  const resolvedReason = isOther
    ? freeText.trim() || undefined
    : reason || undefined;

  function confirm() {
    onConfirm(resolvedReason);
    // 次回のために初期化
    setReason("");
    setFreeText("");
  }

  function cancel() {
    setReason("");
    setFreeText("");
    onCancel();
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
      <div className="w-full max-w-md rounded-lg bg-white p-5 shadow-xl">
        <h2 className="text-lg font-bold text-slate-900">営業対象外にする</h2>
        <p className="mt-2 text-sm text-slate-600">
          {count != null ? (
            <>
              選択した <span className="font-semibold">{count}</span>{" "}
              件を営業対象外にします。
            </>
          ) : (
            <>
              「{targetLabel}」を営業対象外にします。
            </>
          )}
          <br />
          一覧・ランキング・Today Tasks・営業対象一覧から除外されます（削除ではなく、
          除外済み案件からいつでも復元できます）。
        </p>

        <label className="mt-4 block text-xs text-slate-500">
          理由（任意・分析用に保存されます）
          <select
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
            value={reason}
            onChange={(e) => setReason(e.target.value)}
          >
            <option value="">選択しない</option>
            {ARCHIVE_REASONS.map((r) => (
              <option key={r} value={r}>
                {r}
              </option>
            ))}
          </select>
        </label>

        {isOther && (
          <input
            className="mt-2 w-full rounded border border-slate-300 px-2 py-1.5 text-sm text-slate-900"
            placeholder="理由を入力"
            value={freeText}
            onChange={(e) => setFreeText(e.target.value)}
            autoFocus
          />
        )}

        <div className="mt-5 flex justify-end gap-2">
          <button
            className="rounded border border-slate-300 px-4 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-40"
            onClick={cancel}
            disabled={busy}
          >
            キャンセル
          </button>
          <button
            className="rounded bg-red-600 px-4 py-1.5 text-sm font-medium text-white hover:bg-red-700 disabled:opacity-40"
            onClick={confirm}
            disabled={busy}
          >
            {busy ? "処理中…" : "営業対象外にする"}
          </button>
        </div>
      </div>
    </div>
  );
}
