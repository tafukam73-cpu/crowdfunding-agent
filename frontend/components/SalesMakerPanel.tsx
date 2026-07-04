"use client";

import { useEffect, useState } from "react";

import {
  buildMockSalesEmail,
  emptySalesMakerProfile,
  SALES_MAKER_STATUS_COLORS,
  SALES_MAKER_STATUS_LABELS,
  SALES_MAKER_STATUS_ORDER,
  type SalesMakerProfile,
  type SalesMakerStatus,
} from "@/lib/api";

// 営業対象メーカーを 1 枚のカードで管理するパネル。
// 会社名・商品名・各種URL・代表者/担当者・営業ステータス・次回アクション・メモを
// 表示/編集でき、「AI営業メール生成」でメール案（現状はモック）を作成する。
//
// 拡張フィールドは backend の Maker モデルに無いため、当面はブラウザの
// localStorage に保持する（キー: sales-maker-panel:v1:<makerId>）。
// seed には既存メーカー情報から埋められる初期値を渡す（保存値があれば優先）。

const STORAGE_PREFIX = "sales-maker-panel:v1:";

function storageKey(makerId: number | string): string {
  return `${STORAGE_PREFIX}${makerId}`;
}

// localStorage から読み込み（不正データ・SSR 時は null）。
function loadProfile(makerId: number | string): SalesMakerProfile | null {
  if (typeof window === "undefined") return null;
  try {
    const raw = window.localStorage.getItem(storageKey(makerId));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<SalesMakerProfile>;
    // 既定値にマージして欠損キーを補完（型を満たす）。
    return { ...emptySalesMakerProfile(), ...parsed };
  } catch {
    return null;
  }
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col text-xs text-slate-500">
      {label}
      <span className="mt-1">{children}</span>
    </label>
  );
}

export default function SalesMakerPanel({
  makerId,
  seed,
}: {
  makerId: number | string;
  // 既存メーカー情報からの初期値（localStorage に保存値が無いときに使う）。
  seed?: Partial<SalesMakerProfile>;
}) {
  const [form, setForm] = useState<SalesMakerProfile>(() => ({
    ...emptySalesMakerProfile(),
    ...seed,
  }));
  // マウント後に localStorage を反映（ハイドレーション不一致を避ける）。
  const [hydrated, setHydrated] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [email, setEmail] = useState<{ subject: string; body: string } | null>(
    null
  );
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    const stored = loadProfile(makerId);
    if (stored) {
      setForm(stored);
    } else if (seed) {
      setForm({ ...emptySalesMakerProfile(), ...seed });
    }
    setHydrated(true);
    // makerId 単位で 1 度だけ初期化する。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [makerId]);

  function set<K extends keyof SalesMakerProfile>(
    key: K,
    value: SalesMakerProfile[K]
  ) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  function onSave() {
    if (typeof window === "undefined") return;
    try {
      window.localStorage.setItem(storageKey(makerId), JSON.stringify(form));
      setSavedAt(new Date().toLocaleTimeString("ja-JP"));
    } catch {
      setSavedAt("保存に失敗しました（ブラウザの保存領域をご確認ください）");
    }
  }

  function onGenerateEmail() {
    setCopied(false);
    setEmail(buildMockSalesEmail(form));
    // メール案を作ったらステータスを「メール作成済み」へ進める（未送信段階まで）。
    if (form.status === "not_started" || form.status === "researching") {
      set("status", "email_drafted");
    }
  }

  async function onCopyEmail() {
    if (!email) return;
    const text = `件名: ${email.subject}\n\n${email.body}`;
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
    } catch {
      setCopied(false);
    }
  }

  return (
    <section className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-base font-bold text-slate-900">
          営業メーカー管理
        </h2>
        <span
          className={`rounded px-2 py-0.5 text-xs font-medium ${SALES_MAKER_STATUS_COLORS[form.status]}`}
        >
          {SALES_MAKER_STATUS_LABELS[form.status]}
        </span>
      </div>
      <p className="mt-1 text-xs text-slate-500">
        営業対象メーカーの情報を 1 枚で管理します。※ 拡張項目はこのブラウザに保存されます
        （サーバー未連携）。
      </p>

      {/* 入力フィールド */}
      <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Field label="会社名">
          <input
            className="input"
            value={form.company_name}
            onChange={(e) => set("company_name", e.target.value)}
          />
        </Field>
        <Field label="商品名">
          <input
            className="input"
            value={form.product_name}
            onChange={(e) => set("product_name", e.target.value)}
          />
        </Field>
        <Field label="公式サイトURL">
          <input
            className="input"
            value={form.official_url}
            onChange={(e) => set("official_url", e.target.value)}
            placeholder="https://..."
          />
        </Field>
        <Field label="クラウドファンディングURL">
          <input
            className="input"
            value={form.crowdfunding_url}
            onChange={(e) => set("crowdfunding_url", e.target.value)}
            placeholder="https://www.kickstarter.com/..."
          />
        </Field>
        <Field label="代表者名">
          <input
            className="input"
            value={form.representative_name}
            onChange={(e) => set("representative_name", e.target.value)}
          />
        </Field>
        <Field label="担当者名">
          <input
            className="input"
            value={form.contact_name}
            onChange={(e) => set("contact_name", e.target.value)}
          />
        </Field>
        <Field label="メールアドレス">
          <input
            className="input"
            type="email"
            value={form.email}
            onChange={(e) => set("email", e.target.value)}
            placeholder="contact@example.com"
          />
        </Field>
        <Field label="LinkedIn URL">
          <input
            className="input"
            value={form.linkedin_url}
            onChange={(e) => set("linkedin_url", e.target.value)}
            placeholder="https://www.linkedin.com/in/..."
          />
        </Field>
        <Field label="営業ステータス">
          <select
            className="input"
            value={form.status}
            onChange={(e) =>
              set("status", e.target.value as SalesMakerStatus)
            }
          >
            {SALES_MAKER_STATUS_ORDER.map((s) => (
              <option key={s} value={s}>
                {SALES_MAKER_STATUS_LABELS[s]}
              </option>
            ))}
          </select>
        </Field>
        <Field label="次回アクション">
          <input
            className="input"
            value={form.next_action}
            onChange={(e) => set("next_action", e.target.value)}
            placeholder="例：独占販売の打診メールを送付"
          />
        </Field>
      </div>

      <div className="mt-3">
        <Field label="メモ">
          <textarea
            className="input min-h-[72px]"
            value={form.notes}
            onChange={(e) => set("notes", e.target.value)}
          />
        </Field>
      </div>

      {/* 操作 */}
      <div className="mt-4 flex flex-wrap items-center gap-3">
        <button
          onClick={onSave}
          disabled={!hydrated}
          className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
        >
          保存
        </button>
        <button
          onClick={onGenerateEmail}
          className="rounded border border-indigo-300 bg-indigo-50 px-4 py-1.5 text-sm font-medium text-indigo-700 hover:bg-indigo-100"
        >
          AI営業メール生成
        </button>
        {savedAt && (
          <span className="text-xs text-green-700">保存しました（{savedAt}）</span>
        )}
      </div>

      {/* 生成されたメール案 */}
      {email && (
        <div className="mt-4 rounded-md border border-slate-200 bg-slate-50 p-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-800">
              営業メール案
            </h3>
            <span className="rounded bg-amber-100 px-2 py-0.5 text-xs font-medium text-amber-700">
              モック（AI未接続）
            </span>
          </div>
          <div className="mt-2">
            <p className="text-xs text-slate-500">件名</p>
            <p className="mt-0.5 text-sm font-medium text-slate-900">
              {email.subject}
            </p>
          </div>
          <div className="mt-2">
            <p className="text-xs text-slate-500">本文</p>
            <textarea
              readOnly
              value={email.body}
              className="input mt-0.5 min-h-[220px] w-full whitespace-pre-wrap font-mono text-xs"
            />
          </div>
          <div className="mt-2 flex items-center gap-3">
            <button
              onClick={onCopyEmail}
              className="rounded border border-slate-300 px-3 py-1 text-xs font-medium text-slate-700 hover:bg-slate-100"
            >
              本文をコピー
            </button>
            {copied && <span className="text-xs text-green-700">コピーしました</span>}
          </div>
        </div>
      )}
    </section>
  );
}
