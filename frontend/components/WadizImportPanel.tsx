"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

import {
  fetchWadizImports,
  getContactIntelligenceJob,
  wadizImportConfirm,
  wadizImportPreview,
  type WadizEmail,
  type WadizImportHistory,
  type WadizPreview,
} from "@/lib/api";

// ブックマークレット：現在表示中の Wadiz ページから公開情報を集めてクリップボードへ
// JSON コピーする。Akamai 回避・fingerprint 偽装は一切せず、ユーザーが正常閲覧した
// 公開 DOM だけを取得する。
const BOOKMARKLET =
  "javascript:(function(){try{" +
  "var ms=[].slice.call(document.querySelectorAll('a[href^=\"mailto:\"]')).map(function(a){return a.href});" +
  "var ls=[].slice.call(document.links).map(function(a){return a.href});" +
  "var meta={};[].slice.call(document.querySelectorAll('meta')).forEach(function(m){var k=m.getAttribute('property')||m.getAttribute('name');if(k)meta[k]=m.getAttribute('content')});" +
  "var d={url:location.href,title:document.title,text:document.body.innerText,html:document.documentElement.outerHTML,links:ls,mailtos:ms,meta:meta,captured_at:new Date().toISOString()};" +
  "var j=JSON.stringify(d);" +
  "function ok(){alert('Wadiz公開情報をコピーしました。アプリの入力欄に貼り付けてください。')}" +
  "if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(j).then(ok).catch(function(){window.prompt('コピーしてください:',j)})}" +
  "else{window.prompt('コピーしてください:',j)}" +
  "}catch(e){alert('取得に失敗: '+e)}})();";

// 貼り付けが ブックマークレット JSON なら本文/URL を展開する。
function normalizeContent(
  raw: string
): { content: string; content_type: string; source_url: string | null } {
  const t = raw.trim();
  if (t.startsWith("{") && (t.includes('"html"') || t.includes('"text"'))) {
    try {
      const j = JSON.parse(t);
      const url = typeof j.url === "string" ? j.url : null;
      // outerHTML があれば HTML として渡す（JSON-LD / script / meta まで抽出できる）
      if (typeof j.html === "string" && j.html.length > 0) {
        return { content: j.html, content_type: "html", source_url: url };
      }
      const parts = [j.text || ""];
      if (Array.isArray(j.mailtos)) parts.push(j.mailtos.join("\n"));
      if (Array.isArray(j.mailto)) parts.push(j.mailto.join("\n"));
      if (Array.isArray(j.links)) parts.push(j.links.join("\n"));
      if (j.meta && typeof j.meta === "object") {
        parts.push(Object.values(j.meta).join("\n"));
      }
      return { content: parts.join("\n"), content_type: "text", source_url: url };
    } catch {
      // fallthrough
    }
  }
  const isHtml = /<[a-z][\s\S]*>/i.test(t);
  return { content: raw, content_type: isHtml ? "html" : "text", source_url: null };
}

export default function WadizImportPanel({
  projectId,
  defaultSourceUrl,
  onImported,
}: {
  projectId: number;
  defaultSourceUrl?: string | null;
  onImported?: () => void;
}) {
  const [raw, setRaw] = useState("");
  const [sourceUrl, setSourceUrl] = useState(defaultSourceUrl ?? "");
  const [importedBy, setImportedBy] = useState("");
  const [note, setNote] = useState("");
  const [preview, setPreview] = useState<WadizPreview | null>(null);
  const [checked, setChecked] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [history, setHistory] = useState<WadizImportHistory | null>(null);
  const [copied, setCopied] = useState(false);

  const loadHistory = useCallback(async () => {
    try {
      setHistory(await fetchWadizImports(projectId));
    } catch {
      /* 履歴は無くても致命的でない */
    }
  }, [projectId]);

  useEffect(() => {
    loadHistory();
  }, [loadHistory]);

  const runPreview = useCallback(async () => {
    setError(null);
    setResult(null);
    setPreview(null);
    if (!raw.trim()) {
      setError("本文を貼り付けてください。");
      return;
    }
    setLoading(true);
    try {
      const norm = normalizeContent(raw);
      const pv = await wadizImportPreview(projectId, {
        content: norm.content,
        content_type: norm.content_type,
        source_url: norm.source_url || sourceUrl || null,
      });
      setPreview(pv);
      // 新規メールは既定でチェック、既存は外す
      const init: Record<string, boolean> = {};
      pv.emails.forEach((e) => (init[e.value] = e.is_new !== false));
      setChecked(init);
      if (norm.source_url && !sourceUrl) setSourceUrl(norm.source_url);
    } catch (e) {
      setError(e instanceof Error ? e.message : "プレビューに失敗しました");
    } finally {
      setLoading(false);
    }
  }, [raw, sourceUrl, projectId]);

  const acceptedEmails: WadizEmail[] = useMemo(
    () => (preview?.emails ?? []).filter((e) => checked[e.value]),
    [preview, checked]
  );

  // 再評価ジョブを別APIでポーリング（3.5秒間隔・最大 ~1 分）。confirm 応答は待たない。
  // 失敗しても保存済みメールは消えない（取り込みは成功扱い）。
  const pollReassessment = useCallback(
    async (jobId: number, base: string) => {
      const MAX = 18; // 18 * 3.5s ≒ 63s で打ち切り
      for (let i = 0; i < MAX; i++) {
        await new Promise((res) => setTimeout(res, 3500));
        try {
          const j = await getContactIntelligenceJob(jobId);
          if (j.status === "completed") {
            setResult(`${base}・Sales Copilot 再評価 完了`);
            return;
          }
          if (j.status === "failed" || j.status === "cancelled") {
            setResult(`${base}（Sales Copilot 再評価は後で自動再試行されます）`);
            return;
          }
          setResult(`${base}・Sales Copilot 再評価中…`);
        } catch {
          // ポーリング失敗は無視（次の周回で再試行）
        }
      }
      setResult(`${base}（Sales Copilot 再評価は進行中。案件詳細で確認できます）`);
    },
    []
  );

  const confirmSave = useCallback(async () => {
    if (!preview) return;
    if (acceptedEmails.length === 0 && !preview.official_url) {
      setError("保存する項目がありません（メールを選択してください）。");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const r = await wadizImportConfirm(projectId, {
        content_hash: preview.content_hash,
        emails: acceptedEmails,
        socials: preview.socials,
        official_url: preview.official_url,
        maker_name: preview.maker_name,
        source_url: sourceUrl || preview.source_url,
        content_type: preview.content_type,
        imported_by: importedBy || null,
        note: note || null,
      });
      if (r.already_imported) {
        setResult("同一内容は取り込み済みです（重複保存しませんでした）。");
      } else {
        // 保存はここで完了。再評価（Sales Copilot）は待たずにバックグラウンドで進む。
        const base = `保存しました：メール ${r.saved_emails} 件${
          r.contact_found ? "・Contact Intelligence に反映" : ""
        }`;
        setResult(
          r.reassessment_job_id
            ? `${base}・Sales Copilot 再評価待ち…`
            : base
        );
        if (r.reassessment_job_id) {
          void pollReassessment(r.reassessment_job_id, base);
        }
      }
      setPreview(null);
      setRaw("");
      await loadHistory();
      onImported?.();
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存に失敗しました");
    } finally {
      setSaving(false);
    }
  }, [preview, acceptedEmails, projectId, sourceUrl, importedBy, note, loadHistory, onImported, pollReassessment]);

  return (
    <div className="rounded-lg border border-emerald-200 bg-emerald-50/40 p-4">
      <h3 className="text-sm font-bold text-emerald-900">
        📥 Wadiz公開情報を取り込む（手動）
      </h3>
      <p className="mt-1 text-xs text-slate-600">
        Wadiz詳細ページは自動取得できないため、通常のChromeで対象ページを開き「もっと見る」を展開後、
        本文をコピーして下に貼り付けてください（またはブックマークレットでコピー）。公開されている
        メール・SNS・公式サイトだけを抽出します（推測はしません）。
      </p>

      {/* ブックマークレット */}
      <div className="mt-2 flex items-center gap-2 text-xs">
        <a
          href={BOOKMARKLET}
          onClick={(e) => e.preventDefault()}
          draggable
          className="cursor-move rounded border border-emerald-300 bg-white px-2 py-1 font-medium text-emerald-800"
          title="このリンクをブックマークバーにドラッグして使います"
        >
          🔖 Wadiz取り込み（ドラッグでブックマーク登録）
        </a>
        <button
          onClick={() => {
            navigator.clipboard?.writeText(BOOKMARKLET).then(
              () => {
                setCopied(true);
                setTimeout(() => setCopied(false), 2000);
              },
              () => undefined
            );
          }}
          className="text-emerald-700 underline"
        >
          {copied ? "コピー済み" : "ブックマークレットをコピー"}
        </button>
      </div>

      {/* 入力 */}
      <textarea
        value={raw}
        onChange={(e) => setRaw(e.target.value)}
        placeholder="展開後のページ本文（またはHTML／ブックマークレットのJSON）を貼り付け"
        className="mt-2 h-32 w-full rounded border border-slate-300 p-2 text-xs"
      />
      <div className="mt-1 grid grid-cols-1 gap-1 sm:grid-cols-3">
        <input
          value={sourceUrl}
          onChange={(e) => setSourceUrl(e.target.value)}
          placeholder="Wadiz URL"
          className="rounded border border-slate-300 px-2 py-1 text-xs"
        />
        <input
          value={importedBy}
          onChange={(e) => setImportedBy(e.target.value)}
          placeholder="取得者（任意）"
          className="rounded border border-slate-300 px-2 py-1 text-xs"
        />
        <input
          value={note}
          onChange={(e) => setNote(e.target.value)}
          placeholder="メモ（任意）"
          className="rounded border border-slate-300 px-2 py-1 text-xs"
        />
      </div>
      <div className="mt-2 flex items-center gap-2">
        <button
          onClick={runPreview}
          disabled={loading}
          className="rounded bg-emerald-700 px-3 py-1.5 text-xs font-medium text-white hover:bg-emerald-800 disabled:opacity-50"
        >
          {loading ? "抽出中…" : "プレビュー（抽出）"}
        </button>
        {error && <span className="text-xs text-rose-600">{error}</span>}
        {result && <span className="text-xs text-emerald-700">{result}</span>}
      </div>

      {/* プレビュー */}
      {preview && (
        <div className="mt-3 rounded border border-slate-200 bg-white p-3 text-xs">
          {preview.warnings.map((w, i) => (
            <div key={i} className="mb-1 text-amber-700">⚠ {w}</div>
          ))}
          {preview.already_imported && (
            <div className="mb-1 text-slate-500">この内容は取り込み済みです（保存すると重複判定されます）。</div>
          )}
          <div className="font-medium text-slate-700">
            抽出メール（チェックしたものだけ保存）
          </div>
          {preview.emails.length === 0 && (
            <div className="text-slate-400">公開メールは抽出されませんでした。</div>
          )}
          <ul className="mt-1 space-y-1">
            {preview.emails.map((e) => (
              <li key={e.value} className="flex items-start gap-2">
                <input
                  type="checkbox"
                  checked={!!checked[e.value]}
                  onChange={() =>
                    setChecked((c) => ({ ...c, [e.value]: !c[e.value] }))
                  }
                  className="mt-0.5"
                />
                <span>
                  <span className="font-mono text-slate-800">{e.value}</span>
                  {e.is_new === false && (
                    <span className="ml-1 rounded bg-slate-100 px-1 text-[10px] text-slate-500">既存</span>
                  )}
                  <span className="ml-1 rounded bg-emerald-100 px-1 text-[10px] text-emerald-700">
                    {e.confidence ?? "medium"}
                  </span>
                  <span className="ml-1 text-[10px] text-slate-400">
                    {e.extraction_method} / {e.evidence}
                  </span>
                </span>
              </li>
            ))}
          </ul>

          {preview.excluded.length > 0 && (
            <div className="mt-2">
              <div className="font-medium text-slate-700">除外されたメール（理由つき）</div>
              <ul className="mt-1 text-slate-500">
                {preview.excluded.map((x) => (
                  <li key={x.value}>
                    <span className="font-mono">{x.value}</span> — {x.reason}
                  </li>
                ))}
              </ul>
            </div>
          )}

          <div className="mt-2 grid grid-cols-1 gap-1 sm:grid-cols-2">
            <div>
              <span className="font-medium text-slate-700">公式サイト候補：</span>
              {preview.official_url ? (
                <a href={preview.official_url} target="_blank" rel="noreferrer" className="text-sky-600 underline">
                  {preview.official_url}
                </a>
              ) : (
                <span className="text-slate-400">なし</span>
              )}
            </div>
            <div>
              <span className="font-medium text-slate-700">SNS：</span>
              {Object.keys(preview.socials).length ? (
                Object.entries(preview.socials).map(([k, v]) => (
                  <a key={k} href={v} target="_blank" rel="noreferrer" className="mr-2 text-sky-600 underline">
                    {k}
                  </a>
                ))
              ) : (
                <span className="text-slate-400">なし</span>
              )}
            </div>
            <div>
              <span className="font-medium text-slate-700">メーカー名候補：</span>
              <span className="text-slate-600">{preview.maker_name ?? "—"}</span>
            </div>
            <div>
              <span className="font-medium text-slate-700">電話：</span>
              <span className="text-slate-600">{preview.phone ?? "—"}</span>
            </div>
          </div>

          <div className="mt-3 flex items-center gap-2">
            <button
              onClick={confirmSave}
              disabled={saving}
              className="rounded bg-slate-900 px-3 py-1.5 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {saving ? "保存中…" : `確認して保存（${acceptedEmails.length}件）`}
            </button>
            <button
              onClick={() => setPreview(null)}
              className="rounded border border-slate-300 px-3 py-1.5 text-xs text-slate-600 hover:bg-slate-100"
            >
              取消
            </button>
          </div>
        </div>
      )}

      {/* 履歴 */}
      {history && history.items.length > 0 && (
        <div className="mt-3 text-xs">
          <div className="font-medium text-slate-700">取り込み履歴</div>
          <ul className="mt-1 space-y-0.5 text-slate-500">
            {history.items.map((it) => (
              <li key={it.id}>
                {it.created_at?.slice(0, 16).replace("T", " ")} — メール {it.email_count} 件
                {it.imported_by ? `（${it.imported_by}）` : ""}
                {it.emails.length ? `：${it.emails.join(", ")}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
