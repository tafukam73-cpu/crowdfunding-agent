"use client";

import { useEffect, useState } from "react";

import {
  AVAILABILITY_COLORS,
  AVAILABILITY_LABELS,
  fetchAvailabilityChecks,
  formatDateTime,
  runAvailabilityCheck,
  type AvailabilityCheck,
} from "@/lib/api";

function CheckCard({ check, latest }: { check: AvailabilityCheck; latest?: boolean }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-center gap-3">
        <span
          className={`rounded px-2 py-0.5 text-sm font-medium ${AVAILABILITY_COLORS[check.verdict]}`}
        >
          {AVAILABILITY_LABELS[check.verdict]}
        </span>
        <span className="text-sm text-slate-500">最大一致スコア {check.score}</span>
        {latest && (
          <span className="rounded bg-slate-900 px-2 py-0.5 text-xs text-white">
            最新
          </span>
        )}
        <span className="ml-auto text-xs text-slate-400">
          {check.engine} ・ {formatDateTime(check.created_at)}
        </span>
      </div>

      {check.summary && (
        <p className="mt-2 text-sm text-slate-700">{check.summary}</p>
      )}

      {check.hits.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-semibold text-slate-500">判定根拠（ヒット）</p>
          <ul className="mt-1 space-y-1">
            {check.hits.map((h) => (
              <li key={h.id} className="flex items-center gap-2 text-xs">
                <span className="w-24 shrink-0 rounded bg-slate-100 px-2 py-0.5 text-center text-slate-600">
                  {h.site}
                </span>
                <span className="w-10 shrink-0 text-slate-500">{h.match_score}</span>
                {h.url ? (
                  <a
                    href={h.url}
                    target="_blank"
                    rel="noreferrer"
                    className="truncate text-blue-700 hover:underline"
                  >
                    {h.title ?? h.url}
                  </a>
                ) : (
                  <span className="truncate text-slate-700">{h.title ?? "—"}</span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function AvailabilityPanel({ projectId }: { projectId: number }) {
  const [checks, setChecks] = useState<AvailabilityCheck[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reload() {
    fetchAvailabilityChecks(projectId)
      .then(setChecks)
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  async function onRun() {
    setBusy(true);
    setError(null);
    try {
      await runAvailabilityCheck(projectId);
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  const latest = checks[0];
  const history = checks.slice(1);

  return (
    <div className="mt-8">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">日本未上陸判定</h2>
        <button
          onClick={onRun}
          disabled={busy}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "判定中…" : latest ? "再判定" : "判定する"}
        </button>
      </div>
      <p className="mt-1 text-xs text-slate-400">
        ※ Amazon.co.jp / 楽天 / Yahoo!ショッピング / Makuake / GreenFunding を検索し、
        ヒット根拠から判定します（現状はモック検索）。
      </p>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      <div className="mt-3 space-y-3">
        {!latest && (
          <p className="rounded-lg border border-dashed border-slate-300 bg-white p-5 text-sm text-slate-400">
            まだ判定していません。「判定する」を押すと5サイトを検索して判定します。
          </p>
        )}
        {latest && <CheckCard check={latest} latest />}

        {history.length > 0 && (
          <details className="rounded-lg border border-slate-200 bg-white p-3">
            <summary className="cursor-pointer text-sm text-slate-600">
              判定履歴（{history.length}件）
            </summary>
            <div className="mt-3 space-y-3">
              {history.map((c) => (
                <CheckCard key={c.id} check={c} />
              ))}
            </div>
          </details>
        )}
      </div>
    </div>
  );
}
