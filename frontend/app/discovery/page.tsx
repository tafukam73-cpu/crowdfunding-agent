"use client";

import { useCallback, useEffect, useState } from "react";

import CrmRegisterButton from "@/components/CrmRegisterButton";
import Header from "@/components/Header";
import {
  createDiscoveredProduct,
  DISCOVERY_PLATFORM_LABELS,
  DISCOVERY_PLATFORM_ORDER,
  DISCOVERY_PROMOTION_COLORS,
  DISCOVERY_PROMOTION_LABELS,
  DISCOVERY_STATUS_COLORS,
  DISCOVERY_STATUS_LABELS,
  DISCOVERY_STATUS_ORDER,
  getDiscoveryJob,
  getDiscoveryPlatforms,
  type DiscoveredProduct,
  type DiscoveredProductCreate,
  type DiscoveryJob,
  type DiscoveryListParams,
  type DiscoveryPlatformInfo,
  type DiscoveryPromotionStatus,
  type DiscoveryRunRequest,
  type DiscoverySourcePlatform,
  type DiscoveredProductStatus,
  listDiscoveredProducts,
  type PromotePreview,
  type PromoteResult,
  promoteDiscoveredProduct,
  promoteDiscoveredProductPreview,
  promoteDiscoveredProductsBatch,
  runDiscovery,
  scoreDiscoveredProduct,
  startDiscoveryContactIntelligence,
} from "@/lib/api";

// 発掘元の読みやすい表示（未定義キーはそのまま）。
function platformLabel(p: string): string {
  return DISCOVERY_PLATFORM_LABELS[p] ?? p;
}

// スコアは 0〜100 / 未評価 で表示する。
function scoreText(n: number | null): string {
  return n == null ? "未評価" : `${n}/100`;
}

function scoreColor(n: number | null): string {
  if (n == null) return "bg-slate-100 text-slate-500";
  if (n >= 70) return "bg-green-100 text-green-700";
  if (n >= 45) return "bg-amber-100 text-amber-700";
  return "bg-red-100 text-red-700";
}

function StatusBadge({ status }: { status: string }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        DISCOVERY_STATUS_COLORS[status] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {DISCOVERY_STATUS_LABELS[status] ?? status}
    </span>
  );
}

function PromotionBadge({ status }: { status: DiscoveryPromotionStatus }) {
  return (
    <span
      className={`rounded px-2 py-0.5 text-xs font-medium ${
        DISCOVERY_PROMOTION_COLORS[status] ?? "bg-slate-100 text-slate-500"
      }`}
    >
      {DISCOVERY_PROMOTION_LABELS[status] ?? status}
    </span>
  );
}

// 昇格が実行可能なのは未昇格 / 失敗のときだけ（promoted / duplicate / 処理中は不可）。
function isPromotable(status: DiscoveryPromotionStatus): boolean {
  return status === "not_promoted" || status === "promotion_failed";
}

function ScoreChip({ label, value }: { label: string; value: number | null }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded px-2 py-0.5 text-xs font-medium ${scoreColor(
        value
      )}`}
      title={label}
    >
      <span className="opacity-70">{label}</span>
      <span>{scoreText(value)}</span>
    </span>
  );
}

function money(n: number | null): string {
  return n == null ? "-" : Number(n).toLocaleString();
}

// ---------------------------------------------------------------------------
// A. Discovery 実行フォーム（発掘元の対応状況は backend /discovery/platforms を単一の
//    真実源とする。実行はジョブ方式：POST 後に GET でポーリングする）。
// ---------------------------------------------------------------------------
const POLL_INTERVAL_MS = 4000; // ポーリング間隔（3〜5 秒）
const POLL_MAX_TRIES = 90; // 最大試行（約 6 分。無限ポーリングしない）

function RunForm({ onRan }: { onRan: () => void }) {
  const [platforms, setPlatforms] = useState<DiscoveryPlatformInfo[]>([]);
  const [platform, setPlatform] =
    useState<DiscoverySourcePlatform>("kickstarter");
  const [query, setQuery] = useState("");
  const [limit, setLimit] = useState(20);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [job, setJob] = useState<DiscoveryJob | null>(null);

  // 対応状況（単一の真実源）を取得する。
  useEffect(() => {
    getDiscoveryPlatforms()
      .then((list) => {
        setPlatforms(list);
        // 既定は先頭の実取得対応プラットフォーム。
        const firstAvail = list.find((p) => p.available);
        if (firstAvail) setPlatform(firstAvail.platform);
      })
      .catch((e) => setError(String(e)));
  }, []);

  const current = platforms.find((p) => p.platform === platform);
  const available = current?.available ?? false;
  const querySupported = current?.query_supported ?? false;

  async function run() {
    if (!available || busy) return; // 準備中 / 実行中は起動しない（二重押下防止）
    setBusy(true);
    setError(null);
    setJob(null);
    try {
      const payload: DiscoveryRunRequest = {
        source_platform: platform,
        // 検索クエリ非対応サイトへは query を送らない（新着/注目を取得）。
        query: querySupported ? query || null : null,
        limit,
        auto_score: true,
      };
      const res = await runDiscovery(payload);
      await pollJob(res.job_id);
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  // 完了/失敗まで一定間隔でポーリングする（無限ポーリングしない）。
  async function pollJob(jobId: number) {
    for (let i = 0; i < POLL_MAX_TRIES; i++) {
      let j: DiscoveryJob;
      try {
        j = await getDiscoveryJob(jobId);
      } catch (e) {
        setError(String(e));
        break;
      }
      setJob(j);
      if (j.status === "completed" || j.status === "failed") {
        onRan(); // 一覧を再読み込み
        break;
      }
      await new Promise((r) => setTimeout(r, POLL_INTERVAL_MS));
    }
    setBusy(false);
  }

  const running = busy && (!job || job.status === "queued" || job.status === "running");

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="mb-1 text-base font-bold text-slate-900">Discovery 実行</h2>
      <p className="mb-3 text-xs text-slate-500">
        発掘元を指定して収集フレームワークを実行します。Kickstarter / Wadiz / Zeczec /
        Ulule / Indiegogo は実取得に対応しています。その他のプラットフォームは接続状況に
        応じて表示されます（実行可否は各サイトの状態バッジに従います）。重い収集は
        バックグラウンドのジョブで実行され、進捗をここに表示します。
      </p>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-600">
          発掘元プラットフォーム
          <select
            value={platform}
            onChange={(e) =>
              setPlatform(e.target.value as DiscoverySourcePlatform)
            }
            disabled={busy}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm disabled:bg-slate-100"
          >
            {platforms.map((p) => (
              <option key={p.platform} value={p.platform}>
                {p.label}
                {p.available ? "（実取得対応）" : "（準備中）"}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          検索クエリ{querySupported ? "（任意）" : "（このサイトでは使用しません）"}
          <input
            value={querySupported ? query : ""}
            onChange={(e) => setQuery(e.target.value)}
            disabled={!querySupported || busy}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm disabled:bg-slate-100 disabled:text-slate-400"
            placeholder={querySupported ? "例：kitchen gadget" : "新着・注目案件を取得"}
          />
        </label>
        <label className="text-xs text-slate-600">
          取得上限（limit）
          <input
            type="number"
            min={1}
            max={200}
            value={limit}
            onChange={(e) => setLimit(Number(e.target.value))}
            disabled={busy}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm disabled:bg-slate-100"
          />
        </label>
      </div>

      {/* 検索クエリ非対応サイトの説明（Wadiz / Zeczec などの一覧取得型） */}
      {current && !querySupported && (
        <p className="mt-2 text-xs text-slate-500">
          このサイトでは検索クエリは使用せず、新着・注目案件を取得します。
        </p>
      )}
      {current && (
        <p className="mt-1 text-xs text-slate-500">{current.note}</p>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {!available && current && (
        <p className="mt-2 rounded border border-amber-200 bg-amber-50 px-2 py-1.5 text-xs text-amber-800">
          {current.label} は実サイト取得が未接続（準備中）のため実行できません。
        </p>
      )}

      <button
        onClick={run}
        disabled={busy || !available}
        title={
          available
            ? undefined
            : `${current?.label ?? platform} は準備中のため実行できません`
        }
        className="mt-3 rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {running
          ? "実行中…（バックグラウンド収集）"
          : available
          ? "Discovery を実行"
          : "準備中（実行できません）"}
      </button>

      {job && (
        <div className="mt-3 rounded-md border border-slate-200 bg-slate-50 p-3 text-sm">
          <div className="mb-1 flex items-center gap-2 text-xs text-slate-600">
            <span>
              ジョブ #{job.id} / ステータス: <b>{job.status}</b>
            </span>
            {job.current_step && <span>· {job.current_step}</span>}
            <span>· 進捗 {job.progress}%</span>
          </div>
          <div className="flex flex-wrap gap-x-4 gap-y-1">
            <span>
              取得 <b>{job.found_count}</b> 件
            </span>
            <span>
              保存 <b className="text-green-700">{job.saved_count}</b> 件
            </span>
            <span>
              重複 <b className="text-amber-700">{job.duplicate_count}</b> 件
            </span>
            <span>
              スコアリング済み{" "}
              <b className="text-blue-700">{job.scored_count}</b> 件
            </span>
            <span>
              失敗 <b className="text-red-700">{job.failed_count}</b> 件
            </span>
          </div>
          {job.status === "completed" &&
            job.found_count === 0 &&
            !job.error && (
              <p className="mt-1 text-xs text-amber-700">
                実サイトへアクセスしましたが、該当する案件は 0 件でした。
              </p>
            )}
          {job.product_ids.length > 0 && (
            <p className="mt-1 text-xs text-slate-600">
              作成された product ids: {job.product_ids.join(", ")}
            </p>
          )}
          {job.warnings.length > 0 && (
            <p className="mt-1 text-xs text-amber-700">
              警告: {job.warnings.join(" / ")}
            </p>
          )}
          {job.error && (
            <p className="mt-1 text-xs text-red-600">エラー: {job.error}</p>
          )}
        </div>
      )}
    </section>
  );
}

// ---------------------------------------------------------------------------
// B. 手動登録フォーム
// ---------------------------------------------------------------------------
const EMPTY_MANUAL: DiscoveredProductCreate = {
  source_platform: "manual",
  source_url: "",
  project_title: "",
  creator_name: "",
  product_name: "",
  category: "",
  description: "",
  status: "unknown",
  country: "",
  auto_score: false,
};

function ManualForm({ onCreated }: { onCreated: () => void }) {
  const [form, setForm] = useState<DiscoveredProductCreate>({ ...EMPTY_MANUAL });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  function set<K extends keyof DiscoveredProductCreate>(
    key: K,
    value: DiscoveredProductCreate[K]
  ) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function submit() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      // 空文字は送らない（null / 未指定として扱う）。
      const payload: DiscoveredProductCreate = { ...form };
      (Object.keys(payload) as (keyof DiscoveredProductCreate)[]).forEach(
        (k) => {
          if (payload[k] === "") delete payload[k];
        }
      );
      const created = await createDiscoveredProduct(payload);
      setMessage(`登録しました（id=${created.id}）`);
      setForm({ ...EMPTY_MANUAL });
      onCreated();
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-4">
      <h2 className="mb-3 text-base font-bold text-slate-900">商品候補を手動登録</h2>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="text-xs text-slate-600">
          発掘元プラットフォーム
          <select
            value={form.source_platform}
            onChange={(e) =>
              set("source_platform", e.target.value as DiscoverySourcePlatform)
            }
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {DISCOVERY_PLATFORM_ORDER.map((p) => (
              <option key={p} value={p}>
                {platformLabel(p)}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600">
          ステータス
          <select
            value={form.status}
            onChange={(e) =>
              set("status", e.target.value as DiscoveredProductStatus)
            }
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          >
            {DISCOVERY_STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {DISCOVERY_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </label>
        <label className="text-xs text-slate-600 sm:col-span-2">
          source_url（重複判定キー）
          <input
            value={form.source_url ?? ""}
            onChange={(e) => set("source_url", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="https://www.kickstarter.com/projects/..."
          />
        </label>
        <label className="text-xs text-slate-600">
          プロジェクト名（project_title）
          <input
            value={form.project_title ?? ""}
            onChange={(e) => set("project_title", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-slate-600">
          商品名（product_name）
          <input
            value={form.product_name ?? ""}
            onChange={(e) => set("product_name", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-slate-600">
          メーカー / 作者（creator_name）
          <input
            value={form.creator_name ?? ""}
            onChange={(e) => set("creator_name", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="text-xs text-slate-600">
          カテゴリ（category）
          <input
            value={form.category ?? ""}
            onChange={(e) => set("category", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="例：kitchen, outdoor"
          />
        </label>
        <label className="text-xs text-slate-600">
          国（country）
          <input
            value={form.country ?? ""}
            onChange={(e) => set("country", e.target.value)}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
            placeholder="例：US, GB"
          />
        </label>
        <label className="text-xs text-slate-600 sm:col-span-2">
          説明（description）
          <textarea
            value={form.description ?? ""}
            onChange={(e) => set("description", e.target.value)}
            rows={2}
            className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
          />
        </label>
        <label className="flex items-center gap-2 text-xs text-slate-600">
          <input
            type="checkbox"
            checked={form.auto_score ?? false}
            onChange={(e) => set("auto_score", e.target.checked)}
          />
          登録直後に自動スコアリング（auto_score）
        </label>
      </div>
      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}
      {message && <p className="mt-2 text-xs text-green-700">{message}</p>}
      <button
        onClick={submit}
        disabled={busy}
        className="mt-3 rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
      >
        {busy ? "登録中…" : "登録する"}
      </button>
    </section>
  );
}

// ---------------------------------------------------------------------------
// C/D. 商品候補カード（スコア表示・操作ボタン）
// ---------------------------------------------------------------------------
// 昇格の確認パネル（preview を表示し、ユーザー確認後に promote する）。
function PromoteConfirm({
  preview,
  busy,
  onConfirm,
  onCancel,
}: {
  preview: PromotePreview;
  busy: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const f = preview.target_fields as Record<string, unknown>;
  const dup = preview.duplicate_project;
  const str = (v: unknown): string =>
    v == null || v === "" ? "-" : String(v);
  return (
    <div className="mt-2 rounded-md border border-indigo-200 bg-indigo-50 p-3 text-xs">
      <p className="mb-2 font-bold text-indigo-900">
        営業案件に追加する前に確認してください
      </p>

      {/* 移す予定の項目 */}
      <div className="grid grid-cols-1 gap-x-4 gap-y-0.5 sm:grid-cols-2">
        <div>タイトル: <b>{str(f.title)}</b></div>
        <div>メーカー: {str(f.maker_name)}</div>
        <div>カテゴリ: {str(f.category)}</div>
        <div>公式サイト: {str(f.official_website_url)}</div>
        <div>調達額: {str(f.raised_amount)} {str(f.currency)}</div>
        <div>達成率: {f.achievement_rate == null ? "-" : `${f.achievement_rate}%`}</div>
        <div>支援者: {str(f.backers)}</div>
        <div className="truncate">source_url: {str(f.source_url)}</div>
      </div>

      {/* 重複警告（自動統合しない・既存案件へのリンク） */}
      {dup && (
        <div className="mt-2 rounded border border-indigo-300 bg-white px-2 py-1.5">
          <span className="font-medium text-indigo-800">
            既存の営業案件と重複しています（一致: {dup.match_kind}）。
          </span>{" "}
          新規作成せず既存案件を使います。{" "}
          <a
            href={`/projects/${dup.id}`}
            target="_blank"
            rel="noreferrer"
            className="text-blue-600 hover:underline"
          >
            既存案件 #{dup.id} を開く ↗
          </a>
        </div>
      )}

      {/* 近似一致（警告のみ） */}
      {preview.approximate_matches.length > 0 && (
        <p className="mt-2 text-amber-700">
          類似案件があります（近似一致・自動統合しません）:{" "}
          {preview.approximate_matches
            .map((m) => `#${m.id} ${m.title}`)
            .join(", ")}
        </p>
      )}

      {/* 欠損データ警告 */}
      {preview.missing_fields.length > 0 && (
        <p className="mt-2 text-amber-700">
          欠損データ: {preview.missing_fields.join(", ")}（推測はしません）
        </p>
      )}
      {preview.warnings.map((w, i) => (
        <p key={i} className="mt-1 text-amber-700">
          ⚠ {w}
        </p>
      ))}

      <div className="mt-3 flex items-center gap-2">
        <button
          onClick={onConfirm}
          disabled={busy}
          className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
        >
          {busy
            ? "追加中…"
            : dup
            ? "重複を確認して続行"
            : "確定して営業案件に追加"}
        </button>
        <button
          onClick={onCancel}
          disabled={busy}
          className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
        >
          キャンセル
        </button>
      </div>
    </div>
  );
}

function ProductCard({
  product,
  onChanged,
  selected,
  onToggleSelect,
}: {
  product: DiscoveredProduct;
  onChanged: (p: DiscoveredProduct) => void;
  selected: boolean;
  onToggleSelect: (id: number, checked: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ciBusy, setCiBusy] = useState(false);
  const [ciError, setCiError] = useState<string | null>(null);
  const [ciMessage, setCiMessage] = useState<string | null>(null);

  // 昇格（営業案件化）フロー
  const [preview, setPreview] = useState<PromotePreview | null>(null);
  const [promoBusy, setPromoBusy] = useState(false);
  const [promoError, setPromoError] = useState<string | null>(null);
  const [promoMsg, setPromoMsg] = useState<string | null>(null);

  const promotable = isPromotable(product.promotion_status);

  async function loadPreview() {
    if (promoBusy) return; // 二重押下防止
    setPromoBusy(true);
    setPromoError(null);
    setPromoMsg(null);
    try {
      const pv = await promoteDiscoveredProductPreview(product.id);
      setPreview(pv);
    } catch (e) {
      setPromoError(String(e));
    } finally {
      setPromoBusy(false);
    }
  }

  async function confirmPromote() {
    if (promoBusy) return; // 二重押下防止
    setPromoBusy(true);
    setPromoError(null);
    try {
      const res: PromoteResult = await promoteDiscoveredProduct(product.id);
      setPreview(null);
      setPromoMsg(res.message ?? "営業案件へ昇格しました。");
      // カードの昇格状態を即時反映（再取得なしで UI を更新）。
      onChanged({
        ...product,
        promotion_status: (res.promotion_status ??
          "promoted") as DiscoveryPromotionStatus,
        promoted_project_id: res.project_id,
      });
    } catch (e) {
      setPromoError(String(e));
    } finally {
      setPromoBusy(false);
    }
  }

  async function score() {
    setBusy(true);
    setError(null);
    try {
      const updated = await scoreDiscoveredProduct(product.id);
      onChanged(updated);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function startContactIntelligence() {
    setCiBusy(true);
    setCiError(null);
    setCiMessage(null);
    try {
      const res = await startDiscoveryContactIntelligence(product.id);
      setCiMessage(
        `${res.message}（Contact Discovery ID: ${res.contact_discovery_id ?? "-"}）`
      );
      // 一覧カードの連携バッジに反映
      onChanged({ ...product, contact_discovery_id: res.contact_discovery_id });
    } catch (e) {
      setCiError(
        "Contact Intelligence の開始に失敗しました：" + String(e)
      );
    } finally {
      setCiBusy(false);
    }
  }

  const title = product.product_name || product.project_title || "(名称未設定)";
  // official_website_url も source_url も無ければ探索できない
  const hasUrl = Boolean(product.official_website_url || product.source_url);
  const linked = product.contact_discovery_id != null;

  return (
    <div className="rounded-lg border border-slate-200 bg-white p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          {/* 複数選択（昇格可能な商品のみ選択可） */}
          <input
            type="checkbox"
            className="mt-1 disabled:cursor-not-allowed disabled:opacity-40"
            checked={selected}
            disabled={!promotable}
            onChange={(e) => onToggleSelect(product.id, e.target.checked)}
            title={
              promotable
                ? "一括で営業案件に追加する対象に含める"
                : "この商品は昇格対象外です（既に昇格済み/重複/処理中）"
            }
          />
          <div className="min-w-0">
            <h3 className="truncate text-sm font-bold text-slate-900">{title}</h3>
            {product.project_title &&
              product.project_title !== product.product_name && (
                <p className="truncate text-xs text-slate-500">
                  {product.project_title}
                </p>
              )}
          </div>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-1">
          <StatusBadge status={product.status} />
          <PromotionBadge status={product.promotion_status} />
        </div>
      </div>

      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs text-slate-600">
        <span>{platformLabel(product.source_platform)}</span>
        {product.category && <span>カテゴリ: {product.category}</span>}
        {product.country && <span>国: {product.country}</span>}
        <span>調達額: {money(product.funding_amount)}</span>
        <span>支援者: {product.backers_count ?? "-"}</span>
        <span>
          達成率:{" "}
          {product.achievement_rate != null
            ? `${Math.round(product.achievement_rate).toLocaleString()}%`
            : "-"}
        </span>
      </div>

      {/* 営業価値・日本市場適性を目立たせる（「営業すべき順」の判断軸） */}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <span
          className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-sm font-bold ${scoreColor(
            product.sales_value_score
          )}`}
          title="営業価値スコア（営業すべき順）"
        >
          営業価値 {scoreText(product.sales_value_score)}
        </span>
        <ScoreChip label="日本市場適性" value={product.japan_fit_score} />
      </div>

      <div className="mt-1.5 flex flex-wrap gap-1.5">
        <ScoreChip label="総合" value={product.overall_discovery_score} />
        <ScoreChip label="CF適性" value={product.crowdfunding_fit_score} />
        <ScoreChip label="独自性" value={product.novelty_score} />
      </div>

      {product.discovery_reasoning && (
        <p className="mt-2 text-xs text-slate-600">
          <span className="font-medium text-slate-700">評価理由:</span>{" "}
          {product.discovery_reasoning}
        </p>
      )}
      {product.recommended_next_action && (
        <p className="mt-1 text-xs text-slate-600">
          <span className="font-medium text-slate-700">推奨アクション:</span>{" "}
          {product.recommended_next_action}
        </p>
      )}

      {product.source_url && (
        <a
          href={product.source_url}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-block text-xs text-blue-600 hover:underline"
        >
          元ページを開く ↗
        </a>
      )}

      {error && <p className="mt-2 text-xs text-red-600">{error}</p>}

      {/* Contact Intelligence 連携状態 */}
      <div className="mt-2 text-xs">
        {linked ? (
          <span className="inline-flex items-center gap-1 rounded bg-green-100 px-2 py-0.5 font-medium text-green-700">
            Contact Intelligence 連携済み（ID: {product.contact_discovery_id}）
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 rounded bg-slate-100 px-2 py-0.5 font-medium text-slate-500">
            Contact Intelligence 未連携
          </span>
        )}
      </div>

      {ciError && <p className="mt-2 text-xs text-red-600">{ciError}</p>}
      {ciMessage && <p className="mt-2 text-xs text-green-700">{ciMessage}</p>}

      {/* --- 営業案件化（Discovery → Projects 昇格） --- */}
      <div className="mt-3 border-t border-slate-100 pt-3">
        {/* promoted / duplicate_project → 既存 project へのリンク */}
        {(product.promotion_status === "promoted" ||
          product.promotion_status === "duplicate_project") &&
          product.promoted_project_id != null && (
            <a
              href={`/projects/${product.promoted_project_id}`}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:underline"
            >
              {product.promotion_status === "duplicate_project"
                ? "既存の営業案件を開く"
                : "営業案件を開く"}{" "}
              #{product.promoted_project_id} ↗
            </a>
          )}

        {/* promotion_failed → 失敗理由 */}
        {product.promotion_status === "promotion_failed" &&
          product.promotion_error && (
            <p className="mb-1 text-xs text-red-600">
              昇格失敗: {product.promotion_error}
            </p>
          )}

        {/* 処理中 */}
        {product.promotion_status === "promotion_pending" && (
          <span className="text-xs font-medium text-amber-700">
            営業案件化を処理中です…
          </span>
        )}

        {/* 未昇格 / 失敗 → 「営業案件に追加」ボタン（preview 確認を挟む） */}
        {promotable && !preview && (
          <button
            onClick={loadPreview}
            disabled={promoBusy}
            className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:opacity-50"
          >
            {promoBusy
              ? "確認を準備中…"
              : product.promotion_status === "promotion_failed"
              ? "営業案件に追加を再試行"
              : "営業案件に追加"}
          </button>
        )}

        {promoError && <p className="mt-1 text-xs text-red-600">{promoError}</p>}
        {promoMsg && <p className="mt-1 text-xs text-green-700">{promoMsg}</p>}

        {/* preview 確認パネル */}
        {preview && (
          <PromoteConfirm
            preview={preview}
            busy={promoBusy}
            onConfirm={confirmPromote}
            onCancel={() => setPreview(null)}
          />
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <button
          onClick={score}
          disabled={busy}
          className="rounded bg-slate-900 px-3 py-1 text-xs font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          {busy ? "スコアリング中…" : "スコアリング実行"}
        </button>
        {/* D. Contact Intelligence 起動（URL が無ければ無効） */}
        <button
          onClick={startContactIntelligence}
          disabled={ciBusy || !hasUrl}
          title={hasUrl ? undefined : "official_website_url も source_url も未設定です"}
          className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {ciBusy
            ? "開始中…"
            : linked
            ? "Contact Intelligence を再実行"
            : "Contact Intelligence 開始"}
        </button>
        {!hasUrl && <span className="text-xs text-amber-600">URL未設定</span>}
        {/* Discovery → CRM ワンクリック登録 */}
        <CrmRegisterButton source="discovered_product" id={product.id} />
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ページ本体（E. フィルター + C. 一覧）
// ---------------------------------------------------------------------------
type SortUi = "sales" | "japan" | "created";

// 追加フィルター（昇格状態・スコア・データ有無）。client 側で一覧に適用する。
type PromoFilter = "" | "not_promoted" | "promoted" | "duplicate_project";
type ScoreFilter = "" | "60" | "70";

export default function DiscoveryPage() {
  const [products, setProducts] = useState<DiscoveredProduct[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // E. フィルター（サーバー側）
  const [platform, setPlatform] = useState("");
  const [status, setStatus] = useState("");
  const [category, setCategory] = useState("");
  const [minScore, setMinScore] = useState("");
  const [sortUi, setSortUi] = useState<SortUi>("sales");

  // 追加フィルター（client 側）
  const [promoFilter, setPromoFilter] = useState<PromoFilter>("");
  const [scoreFilter, setScoreFilter] = useState<ScoreFilter>("");
  const [onlyMaker, setOnlyMaker] = useState(false);
  const [contactReady, setContactReady] = useState(false);
  const [highConfidence, setHighConfidence] = useState(false);

  // 複数選択・一括昇格
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [batchBusy, setBatchBusy] = useState(false);
  const [batchMsg, setBatchMsg] = useState<string | null>(null);
  const [batchError, setBatchError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: DiscoveryListParams = { sort: sortUi };
      if (platform) params.platform = platform;
      if (status) params.status = status;
      if (category) params.category = category;
      if (minScore !== "") params.min_score = Number(minScore);
      const list = await listDiscoveredProducts(params);
      setProducts(list);
      setSelected(new Set()); // 再読み込み時は選択をクリア
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [platform, status, category, minScore, sortUi]);

  useEffect(() => {
    load();
  }, [load]);

  function onProductChanged(updated: DiscoveredProduct) {
    setProducts((prev) =>
      prev.map((p) => (p.id === updated.id ? updated : p))
    );
    // 昇格などで対象外になった選択は外す
    if (!isPromotable(updated.promotion_status)) {
      setSelected((prev) => {
        if (!prev.has(updated.id)) return prev;
        const next = new Set(prev);
        next.delete(updated.id);
        return next;
      });
    }
  }

  // client 側の追加フィルターを適用した一覧。
  const visible = products.filter((p) => {
    if (promoFilter && p.promotion_status !== promoFilter) return false;
    if (scoreFilter) {
      const s = p.overall_discovery_score;
      if (s == null || s < Number(scoreFilter)) return false;
    }
    if (onlyMaker && !p.creator_name) return false;
    // 連絡先探索向き：URL があり、メーカー名も取れている（探索の起点になる）。
    if (
      contactReady &&
      !((p.official_website_url || p.source_url) && p.creator_name)
    )
      return false;
    // 情報が揃っている（confidence 高）：達成率・スコア・メーカー名が揃う。
    if (
      highConfidence &&
      !(p.achievement_rate != null && p.overall_discovery_score != null && p.creator_name)
    )
      return false;
    return true;
  });

  // 選択可能（昇格可能）な表示中の商品 ID。
  const selectableIds = visible
    .filter((p) => isPromotable(p.promotion_status))
    .map((p) => p.id);
  const selectedVisible = selectableIds.filter((id) => selected.has(id));
  const allSelected =
    selectableIds.length > 0 && selectedVisible.length === selectableIds.length;

  function toggleSelect(id: number, checked: boolean) {
    setSelected((prev) => {
      const next = new Set(prev);
      if (checked) next.add(id);
      else next.delete(id);
      return next;
    });
  }

  function toggleSelectAll() {
    setSelected((prev) => {
      if (allSelected) {
        const next = new Set(prev);
        selectableIds.forEach((id) => next.delete(id));
        return next;
      }
      const next = new Set(prev);
      selectableIds.forEach((id) => next.add(id));
      return next;
    });
  }

  async function runBatchPromote() {
    if (batchBusy) return; // 二重押下防止
    const ids = Array.from(selected);
    if (ids.length === 0) return;
    setBatchBusy(true);
    setBatchMsg(null);
    setBatchError(null);
    try {
      const res = await promoteDiscoveredProductsBatch(ids);
      const c = res.counts || {};
      setBatchMsg(
        `一括昇格：成功 ${c.promoted ?? 0} / 重複 ${c.duplicate_project ?? 0} / ` +
          `既昇格 ${c.already_promoted ?? 0} / 失敗 ${c.failed ?? 0} / ` +
          `不存在 ${c.not_found ?? 0}（処理 ${res.processed} 件）` +
          (res.skipped_over_limit
            ? ` ／ 上限超過で ${res.skipped_over_limit} 件は未処理`
            : "")
      );
      await load(); // 最新の昇格状態を反映（選択もクリアされる）
    } catch (e) {
      setBatchError(String(e));
    } finally {
      setBatchBusy(false);
    }
  }

  return (
    <div className="min-h-screen bg-slate-50">
      <Header />
      <main className="mx-auto max-w-5xl space-y-6 px-6 py-6">
        <div>
          <h1 className="text-xl font-bold text-slate-900">商品発掘 / Discovery</h1>
          <p className="mt-1 text-sm text-slate-500">
            海外クラウドファンディングの商品候補を発掘・評価します。
          </p>
        </div>

        <RunForm onRan={load} />
        <ManualForm onCreated={load} />

        {/* E. フィルター */}
        <section className="rounded-lg border border-slate-200 bg-white p-4">
          <h2 className="mb-3 text-base font-bold text-slate-900">
            商品候補一覧
          </h2>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <label className="text-xs text-slate-600">
              発掘元
              <select
                value={platform}
                onChange={(e) => setPlatform(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">すべて</option>
                {DISCOVERY_PLATFORM_ORDER.map((p) => (
                  <option key={p} value={p}>
                    {platformLabel(p)}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-600">
              ステータス
              <select
                value={status}
                onChange={(e) => setStatus(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">すべて</option>
                {DISCOVERY_STATUS_ORDER.map((s) => (
                  <option key={s} value={s}>
                    {DISCOVERY_STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </label>
            <label className="text-xs text-slate-600">
              カテゴリ
              <input
                value={category}
                onChange={(e) => setCategory(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="例：kitchen"
              />
            </label>
            <label className="text-xs text-slate-600">
              最低スコア
              <input
                type="number"
                min={0}
                max={100}
                value={minScore}
                onChange={(e) => setMinScore(e.target.value)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
                placeholder="0〜100"
              />
            </label>
            <label className="text-xs text-slate-600">
              並び替え
              <select
                value={sortUi}
                onChange={(e) => setSortUi(e.target.value as SortUi)}
                className="mt-1 w-full rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="sales">営業価値が高い順</option>
                <option value="japan">日本市場適性が高い順</option>
                <option value="created">登録が新しい順</option>
              </select>
            </label>
          </div>

          {/* F. 追加フィルター（昇格状態・スコア・データ有無）＝client 側 */}
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
            <label className="text-xs text-slate-600">
              昇格状態
              <select
                value={promoFilter}
                onChange={(e) => setPromoFilter(e.target.value as PromoFilter)}
                className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">すべて</option>
                <option value="not_promoted">未昇格</option>
                <option value="promoted">昇格済み</option>
                <option value="duplicate_project">重複</option>
              </select>
            </label>
            <label className="text-xs text-slate-600">
              スコア
              <select
                value={scoreFilter}
                onChange={(e) => setScoreFilter(e.target.value as ScoreFilter)}
                className="ml-1 rounded border border-slate-300 px-2 py-1 text-sm"
              >
                <option value="">指定なし</option>
                <option value="60">60以上</option>
                <option value="70">70以上</option>
              </select>
            </label>
            <label className="flex items-center gap-1 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={onlyMaker}
                onChange={(e) => setOnlyMaker(e.target.checked)}
              />
              メーカー名あり
            </label>
            <label className="flex items-center gap-1 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={contactReady}
                onChange={(e) => setContactReady(e.target.checked)}
              />
              連絡先探索向き
            </label>
            <label className="flex items-center gap-1 text-xs text-slate-600">
              <input
                type="checkbox"
                checked={highConfidence}
                onChange={(e) => setHighConfidence(e.target.checked)}
              />
              情報が揃っている（confidence高）
            </label>
          </div>

          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={load}
              disabled={loading}
              className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100 disabled:opacity-50"
            >
              {loading ? "読み込み中…" : "再読み込み"}
            </button>
            <span className="text-xs text-slate-500">
              {visible.length} / {products.length} 件
            </span>
          </div>

          {error && <p className="mt-2 text-sm text-red-600">{error}</p>}
        </section>

        {/* G. 一括昇格バー（複数選択して営業案件へまとめて追加） */}
        <section className="rounded-lg border border-indigo-200 bg-indigo-50 p-3">
          <div className="flex flex-wrap items-center gap-3">
            <label className="flex items-center gap-1.5 text-xs font-medium text-indigo-900">
              <input
                type="checkbox"
                checked={allSelected}
                disabled={selectableIds.length === 0}
                onChange={toggleSelectAll}
              />
              表示中の昇格可能な商品をすべて選択（{selectableIds.length} 件）
            </label>
            <span className="text-xs text-indigo-700">
              選択中: {selected.size} 件
            </span>
            <button
              onClick={runBatchPromote}
              disabled={batchBusy || selected.size === 0}
              className="rounded bg-indigo-600 px-3 py-1 text-xs font-medium text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {batchBusy
                ? "一括追加中…"
                : `選択した ${selected.size} 件を営業案件に追加`}
            </button>
            {selected.size > 0 && !batchBusy && (
              <button
                onClick={() => setSelected(new Set())}
                className="rounded border border-indigo-300 px-3 py-1 text-xs font-medium text-indigo-700 hover:bg-indigo-100"
              >
                選択をクリア
              </button>
            )}
          </div>
          <p className="mt-1 text-xs text-indigo-600">
            自動昇格はしません。選択した商品のみを、ユーザー確認のうえ営業案件へ追加します。
          </p>
          {batchMsg && <p className="mt-1 text-xs text-green-700">{batchMsg}</p>}
          {batchError && <p className="mt-1 text-xs text-red-600">{batchError}</p>}
        </section>

        {/* C. 一覧 */}
        {!loading && visible.length === 0 && !error && (
          <p className="text-sm text-slate-500">
            {products.length === 0
              ? "商品候補がありません。上のフォームから手動登録するか、Discovery を実行してください。"
              : "フィルター条件に一致する商品候補がありません。"}
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {visible.map((p) => (
            <ProductCard
              key={p.id}
              product={p}
              onChanged={onProductChanged}
              selected={selected.has(p.id)}
              onToggleSelect={toggleSelect}
            />
          ))}
        </div>
      </main>
    </div>
  );
}
