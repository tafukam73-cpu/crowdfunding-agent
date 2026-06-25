"use client";

import { useCallback, useEffect, useState } from "react";

import Header from "@/components/Header";
import {
  collectJapaneseSuccess,
  fetchJapaneseSuccess,
  formatMoney,
  SITE_LABELS,
  type JapaneseSuccess,
  type JapaneseSuccessList,
  type SourceSite,
} from "@/lib/api";

const PAGE_SIZE = 20;

// 収集・絞り込み対象の日本クラファンプラットフォーム
const JP_PLATFORMS = ["makuake", "greenfunding"] as const;

function rate(s: JapaneseSuccess): number | null {
  if (!s.goal_amount || !s.raised_amount) return null;
  return Math.round((s.raised_amount / s.goal_amount) * 100);
}

function platformLabel(platform: string): string {
  return SITE_LABELS[platform as SourceSite] ?? platform;
}

export default function JapaneseSuccessPage() {
  const [data, setData] = useState<JapaneseSuccessList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [collecting, setCollecting] = useState(false);

  const [platform, setPlatform] = useState<string>("");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("raised_amount");
  const [order, setOrder] = useState<"asc" | "desc">("desc");
  const [page, setPage] = useState(1);

  const load = useCallback(() => {
    setLoading(true);
    fetchJapaneseSuccess({ platform, q, sort, order, page, page_size: PAGE_SIZE })
      .then((d) => {
        setData(d);
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [platform, q, sort, order, page]);

  useEffect(() => {
    load();
  }, [load]);

  async function onCollect() {
    setCollecting(true);
    setError(null);
    try {
      // 絞り込み中のプラットフォームのみ収集。未選択なら全て一括収集。
      const res = await collectJapaneseSuccess(platform || undefined);
      window.alert(
        `収集完了：取得 ${res.fetched} 件（新規 ${res.created} / 更新 ${res.updated}）`
      );
      setPage(1);
      load();
    } catch (e) {
      setError(String(e));
    } finally {
      setCollecting(false);
    }
  }

  const collectLabel = collecting
    ? "収集中…"
    : platform
    ? `${platformLabel(platform)}を収集`
    : "全て収集";

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  return (
    <>
      <Header />
      <main className="mx-auto max-w-5xl px-6 py-8">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold">日本の成功事例（比較用）</h1>
            <p className="mt-1 text-sm text-slate-500">
              Makuake 等の応援購入成功案件。海外案件の営業判断の根拠に使います。
            </p>
          </div>
          <button
            onClick={onCollect}
            disabled={collecting}
            className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
          >
            {collectLabel}
          </button>
        </div>

        {/* フィルタ */}
        <div className="mt-6 flex flex-wrap items-end gap-3">
          <label className="flex flex-col text-xs text-slate-500">
            プラットフォーム
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={platform}
              onChange={(e) => {
                setPage(1);
                setPlatform(e.target.value);
              }}
            >
              <option value="">すべて</option>
              {JP_PLATFORMS.map((p) => (
                <option key={p} value={p}>
                  {platformLabel(p)}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            並び替え
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={sort}
              onChange={(e) => setSort(e.target.value)}
            >
              <option value="raised_amount">応援購入額</option>
              <option value="backers_count">支援者数</option>
              <option value="end_date">終了日</option>
              <option value="created_at">登録日</option>
              <option value="title">案件名</option>
            </select>
          </label>

          <label className="flex flex-col text-xs text-slate-500">
            順序
            <select
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              value={order}
              onChange={(e) => setOrder(e.target.value as "asc" | "desc")}
            >
              <option value="desc">降順</option>
              <option value="asc">昇順</option>
            </select>
          </label>

          <label className="flex flex-1 flex-col text-xs text-slate-500">
            案件名検索
            <input
              className="mt-1 rounded border border-slate-300 px-2 py-1 text-sm text-slate-900"
              placeholder="キーワード"
              value={q}
              onChange={(e) => {
                setPage(1);
                setQ(e.target.value);
              }}
            />
          </label>
        </div>

        {error && (
          <p className="mt-6 text-red-600">
            読み込み失敗：{error}（バックエンド http://localhost:8000 を確認）
          </p>
        )}

        {/* 一覧テーブル */}
        <div className="mt-6 overflow-hidden rounded-lg border border-slate-200 bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-left text-xs text-slate-500">
              <tr>
                <th className="px-4 py-2">案件名</th>
                <th className="px-4 py-2">プラットフォーム</th>
                <th className="px-4 py-2">応援購入額</th>
                <th className="px-4 py-2">達成率</th>
                <th className="px-4 py-2">支援者</th>
                <th className="px-4 py-2">メーカー</th>
              </tr>
            </thead>
            <tbody>
              {data?.items.map((s) => {
                const r = rate(s);
                return (
                  <tr key={s.id} className="border-t border-slate-100 hover:bg-slate-50">
                    <td className="px-4 py-3">
                      {s.source_url ? (
                        <a
                          href={s.source_url}
                          target="_blank"
                          rel="noreferrer"
                          className="font-medium text-blue-700 hover:underline"
                        >
                          {s.title}
                        </a>
                      ) : (
                        <span className="font-medium text-slate-800">{s.title}</span>
                      )}
                      <div className="text-xs text-slate-400">{s.category ?? "—"}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded bg-orange-100 px-2 py-0.5 text-xs font-medium text-orange-700">
                        {platformLabel(s.platform)}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {formatMoney(s.raised_amount, s.currency)}
                    </td>
                    <td className="px-4 py-3">
                      {r != null ? `${r.toLocaleString()}%` : "—"}
                    </td>
                    <td className="px-4 py-3">
                      {s.backers_count?.toLocaleString() ?? "—"}
                    </td>
                    <td className="px-4 py-3 text-slate-600">
                      {s.maker_name ?? "—"}
                    </td>
                  </tr>
                );
              })}
              {!loading && data?.items.length === 0 && (
                <tr>
                  <td colSpan={6} className="px-4 py-8 text-center text-slate-400">
                    成功事例がまだありません。「成功事例を収集」を押してください。
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>

        {/* ページング */}
        <div className="mt-4 flex items-center justify-between text-sm text-slate-500">
          <span>
            {data ? `全 ${data.total} 件` : ""}
            {loading && "（読み込み中…）"}
          </span>
          <div className="flex items-center gap-2">
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
            >
              前へ
            </button>
            <span>
              {page} / {totalPages}
            </span>
            <button
              className="rounded border border-slate-300 px-3 py-1 disabled:opacity-40"
              disabled={page >= totalPages}
              onClick={() => setPage((p) => p + 1)}
            >
              次へ
            </button>
          </div>
        </div>
      </main>
    </>
  );
}
