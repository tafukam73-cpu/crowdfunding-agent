"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import {
  createMakerFromProject,
  fetchMaker,
  formatDateTime,
  updateMaker,
} from "@/lib/api";

// 営業メモ。保存先は CRM のメーカー（crm_makers.notes）で、既存の
// GET/PATCH /crm/makers/{id} のみを使う（DB 変更・新規 API なし）。
// CRM 未登録の案件は、その場で登録してからメモを書けるようにする。
export default function ProjectNotesPanel({
  projectId,
  makerId,
  onMakerLinked,
}: {
  projectId: number;
  makerId: number | null;
  onMakerLinked?: (makerId: number) => void;
}) {
  const [linkedId, setLinkedId] = useState<number | null>(makerId);
  const [notes, setNotes] = useState("");
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [dirty, setDirty] = useState(false);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLinkedId(makerId);
  }, [makerId]);

  useEffect(() => {
    if (linkedId == null) return;
    let active = true;
    setLoading(true);
    fetchMaker(linkedId)
      .then((m) => {
        if (!active) return;
        setNotes(m.notes ?? "");
        setSavedAt(m.updated_at);
        setDirty(false);
        setError(null);
      })
      .catch(() => active && setError("メモを取得できませんでした"))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [linkedId]);

  async function link() {
    setBusy(true);
    setError(null);
    try {
      const { maker } = await createMakerFromProject(projectId);
      setLinkedId(maker.id);
      onMakerLinked?.(maker.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (linkedId == null) return;
    setBusy(true);
    setError(null);
    try {
      const m = await updateMaker(linkedId, { notes });
      setSavedAt(m.updated_at);
      setDirty(false);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  if (linkedId == null) {
    return (
      <div className="text-sm text-slate-600">
        <p>
          メモは CRM のメーカー単位で保存します。この案件はまだ CRM
          に登録されていません。
        </p>
        {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
        <button
          onClick={link}
          disabled={busy}
          className="mt-3 rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
        >
          {busy ? "登録中…" : "CRMに登録してメモを書く"}
        </button>
      </div>
    );
  }

  return (
    <div>
      {loading ? (
        <p className="text-sm text-slate-400">読み込み中…</p>
      ) : (
        <>
          <textarea
            value={notes}
            onChange={(e) => {
              setNotes(e.target.value);
              setDirty(true);
            }}
            rows={6}
            placeholder="商談メモ・担当者・次回の論点など"
            className="w-full rounded border border-slate-300 px-3 py-2 text-sm text-slate-900"
          />
          {error && <p className="mt-1 text-xs text-red-600">{error}</p>}
          <div className="mt-2 flex flex-wrap items-center gap-3">
            <button
              onClick={save}
              disabled={busy || !dirty}
              className="rounded bg-slate-900 px-3 py-1 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-40"
            >
              {busy ? "保存中…" : "保存"}
            </button>
            <span className="text-xs text-slate-400">
              {dirty
                ? "未保存の変更があります"
                : savedAt
                  ? `最終更新 ${formatDateTime(savedAt)}`
                  : ""}
            </span>
            <Link
              href={`/crm/makers/${linkedId}`}
              className="ml-auto text-xs text-blue-700 hover:underline"
            >
              CRMでメーカーを開く →
            </Link>
          </div>
        </>
      )}
    </div>
  );
}
