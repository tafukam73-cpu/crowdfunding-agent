"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchScrapeStats,
  formatDateTime,
  siteLabel,
  type ScrapeStats,
  type SiteStats,
} from "@/lib/api";

// 異常レベル（高いほど重大）。カードの枠色・並びの判断に使う。
type HealthLevel = "critical" | "warning" | "ok" | "idle";

type Badge = { label: string; cls: string };

const LEVEL_ORDER: Record<HealthLevel, number> = {
  critical: 3,
  warning: 2,
  ok: 1,
  idle: 0,
};

const CARD_CLS: Record<HealthLevel, string> = {
  critical: "border-red-300 bg-red-50",
  warning: "border-amber-300 bg-amber-50",
  ok: "border-slate-200 bg-white",
  idle: "border-slate-200 bg-slate-50",
};

/**
 * サイトの取得状況から異常を判定し、レベルと表示バッジを返す。
 *
 * 判定:
 *  - critical: 構造変化の疑い（structure_change_suspected）／成功率がしきい値割れ（degraded）
 *  - warning : 直近実行が失敗／403 を検知
 *  - idle    : 集計対象 run が無い（未実行）
 *  - ok      : 上記いずれにも該当しない
 */
function evaluate(s: SiteStats): { level: HealthLevel; badges: Badge[] } {
  const badges: Badge[] = [];
  let level: HealthLevel = "ok";

  if (s.total === 0) {
    return {
      level: "idle",
      badges: [{ label: "未実行", cls: "bg-slate-200 text-slate-600" }],
    };
  }

  if (s.structure_change_suspected) {
    level = "critical";
    badges.push({ label: "構造変化検知", cls: "bg-red-600 text-white" });
  }
  if (s.degraded) {
    level = "critical";
    badges.push({ label: "成功率低下", cls: "bg-red-100 text-red-700" });
  }
  if (s.last_status === "error") {
    if (level !== "critical") level = "warning";
    badges.push({ label: "直近失敗", cls: "bg-amber-100 text-amber-700" });
  }
  if (s.http_403_count > 0) {
    if (level !== "critical") level = "warning";
    badges.push({
      label: `403検知 ${s.http_403_count}`,
      cls: "bg-amber-100 text-amber-700",
    });
  }

  if (badges.length === 0) {
    badges.push({ label: "正常", cls: "bg-green-100 text-green-700" });
  }
  return { level, badges };
}

function ratePct(rate: number | null): string {
  return rate == null ? "—" : `${Math.round(rate * 100)}%`;
}

function rateCls(rate: number | null): string {
  if (rate == null) return "text-slate-400";
  if (rate >= 0.8) return "text-green-600";
  if (rate >= 0.5) return "text-amber-600";
  return "text-red-600";
}

export default function ScrapeStatsPanel({
  window = 20,
}: {
  window?: number;
}) {
  const [stats, setStats] = useState<ScrapeStats | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    fetchScrapeStats(window)
      .then((d) => {
        setStats(d);
        setError(null);
      })
      .catch((e) => setError(String(e)));
  }, [window]);

  useEffect(() => {
    load();
  }, [load]);

  // 異常サイト（critical/warning）を上に、重大度順で並べる
  const sites = stats
    ? [...stats.sites]
        .map((s) => ({ s, ...evaluate(s) }))
        .sort((a, b) => LEVEL_ORDER[b.level] - LEVEL_ORDER[a.level])
    : [];

  const alerts = sites.filter(
    (x) => x.level === "critical" || x.level === "warning"
  );

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-slate-700">
          取得モニタリング（直近{stats?.window ?? window}件）
        </h2>
        <button
          onClick={load}
          className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-600 hover:bg-slate-50"
        >
          更新
        </button>
      </div>

      {error && <p className="mt-2 text-sm text-red-600">{error}</p>}

      {/* 全体アラート */}
      {stats && (
        <div className="mt-2">
          {alerts.length === 0 ? (
            <p className="text-xs text-green-700">
              ✓ すべてのサイトが正常です
            </p>
          ) : (
            <p className="text-xs font-medium text-red-700">
              ⚠ 要注意：
              {alerts.map((x) => siteLabel(x.s.site)).join(" / ")}
              {stats.structure_change_suspected && "（構造変化の疑いあり）"}
            </p>
          )}
        </div>
      )}

      <div className="mt-3 grid grid-cols-1 gap-3 sm:grid-cols-2">
        {sites.map(({ s, level, badges }) => (
          <div
            key={s.site}
            className={`rounded border px-3 py-3 text-sm ${CARD_CLS[level]}`}
          >
            {/* ヘッダ：サイト名 + バッジ */}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-medium text-slate-800">
                {siteLabel(s.site)}
              </span>
              <span className="flex flex-wrap items-center gap-1">
                {badges.map((b) => (
                  <span
                    key={b.label}
                    className={`rounded px-2 py-0.5 text-xs font-medium ${b.cls}`}
                  >
                    {b.label}
                  </span>
                ))}
              </span>
            </div>

            {/* 成功率 */}
            <div className="mt-2 flex items-baseline gap-2">
              <span className="text-xs text-slate-500">成功率</span>
              <span className={`text-lg font-bold ${rateCls(s.success_rate)}`}>
                {ratePct(s.success_rate)}
              </span>
              <span className="text-xs text-slate-400">
                （{s.success}/{s.total} 件）
              </span>
            </div>

            {/* 詳細 */}
            <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs text-slate-600">
              <dt className="text-slate-400">直近実行</dt>
              <dd>
                {s.last_status ? (
                  <span
                    className={
                      s.last_status === "success"
                        ? "text-green-600"
                        : s.last_status === "error"
                          ? "text-red-600"
                          : "text-blue-600"
                    }
                  >
                    {s.last_status === "success"
                      ? "成功"
                      : s.last_status === "error"
                        ? "失敗"
                        : "実行中"}
                  </span>
                ) : (
                  "—"
                )}
                {" / "}
                {formatDateTime(s.last_run_at)}
              </dd>

              <dt className="text-slate-400">最終成功</dt>
              <dd>{formatDateTime(s.last_success_at)}</dd>

              <dt className="text-slate-400">最終失敗</dt>
              <dd className={s.last_failure_at ? "text-red-600" : ""}>
                {formatDateTime(s.last_failure_at)}
              </dd>

              <dt className="text-slate-400">403発生</dt>
              <dd className={s.http_403_count > 0 ? "text-amber-600" : ""}>
                {s.http_403_count} 件
              </dd>

              <dt className="text-slate-400">エラー内訳</dt>
              <dd>
                通信{s.network_errors} / 構造{s.structure_errors} / 他
                {s.unknown_errors}
              </dd>

              {s.last_structure_error_at && (
                <>
                  <dt className="text-red-500">直近の構造エラー</dt>
                  <dd className="font-medium text-red-600">
                    {formatDateTime(s.last_structure_error_at)}
                  </dd>
                </>
              )}
            </dl>
          </div>
        ))}
      </div>
    </div>
  );
}
