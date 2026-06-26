"use client";

import { useEffect, useState } from "react";

import Header from "@/components/Header";
import {
  fetchEmailSettings,
  updateEmailSettings,
  type EmailSettingsInput,
} from "@/lib/api";

type Form = {
  company_name: string;
  sender_name: string;
  sender_title: string;
  sender_department: string;
  sender_email: string;
  phone: string;
  website_url: string;
  company_profile: string;
  signature_template: string;
};

const EMPTY: Form = {
  company_name: "",
  sender_name: "",
  sender_title: "",
  sender_department: "",
  sender_email: "",
  phone: "",
  website_url: "",
  company_profile: "",
  signature_template: "",
};

// 署名テンプレートのプレースホルダ例（未入力時のプレースホルダ表示用）。
const SIGNATURE_PLACEHOLDER = [
  "Best regards,",
  "",
  "{sender_name}",
  "{sender_title}",
  "{sender_department}",
  "{company_name}",
  "",
  "Email: {sender_email}",
  "Phone: {phone}",
  "Website: {website_url}",
].join("\n");

export default function EmailSettingsPage() {
  const [form, setForm] = useState<Form>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<string | null>(null);

  useEffect(() => {
    fetchEmailSettings()
      .then((s) => {
        if (s) {
          setForm({
            company_name: s.company_name ?? "",
            sender_name: s.sender_name ?? "",
            sender_title: s.sender_title ?? "",
            sender_department: s.sender_department ?? "",
            sender_email: s.sender_email ?? "",
            phone: s.phone ?? "",
            website_url: s.website_url ?? "",
            company_profile: s.company_profile ?? "",
            signature_template: s.signature_template ?? "",
          });
        }
        setError(null);
      })
      .catch((e) => setError(String(e)))
      .finally(() => setLoading(false));
  }, []);

  function set<K extends keyof Form>(key: K, value: string) {
    setForm((f) => ({ ...f, [key]: value }));
  }

  async function onSave() {
    setSaving(true);
    setError(null);
    // 空文字は null として送る（未設定扱い）。
    const payload: EmailSettingsInput = Object.fromEntries(
      Object.entries(form).map(([k, v]) => [k, v.trim() === "" ? null : v])
    );
    try {
      const saved = await updateEmailSettings(payload);
      setSavedAt(saved.updated_at);
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <main>
      <Header />
      <div className="mx-auto max-w-3xl px-6 py-8">
        <h1 className="text-xl font-bold text-slate-900">メール設定</h1>
        <p className="mt-1 text-sm text-slate-500">
          ここに登録した会社情報は営業メール生成時のコンテキストとして AI に渡され、
          署名テンプレートはメール本文の末尾に自動挿入されます。
          未登録でもメール生成は動作します（既定の差出人で生成）。
        </p>

        {error && (
          <p className="mt-4 rounded bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {loading ? (
          <p className="mt-6 text-sm text-slate-500">読み込み中…</p>
        ) : (
          <div className="mt-6 space-y-6">
            <section className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold text-slate-700">
                会社・差出人情報
              </h2>
              <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
                <Field label="会社名">
                  <input
                    className="input"
                    value={form.company_name}
                    onChange={(e) => set("company_name", e.target.value)}
                  />
                </Field>
                <Field label="担当者名">
                  <input
                    className="input"
                    value={form.sender_name}
                    onChange={(e) => set("sender_name", e.target.value)}
                  />
                </Field>
                <Field label="役職">
                  <input
                    className="input"
                    value={form.sender_title}
                    onChange={(e) => set("sender_title", e.target.value)}
                  />
                </Field>
                <Field label="部署">
                  <input
                    className="input"
                    value={form.sender_department}
                    onChange={(e) => set("sender_department", e.target.value)}
                  />
                </Field>
                <Field label="メールアドレス">
                  <input
                    className="input"
                    type="email"
                    value={form.sender_email}
                    onChange={(e) => set("sender_email", e.target.value)}
                  />
                </Field>
                <Field label="電話番号">
                  <input
                    className="input"
                    value={form.phone}
                    onChange={(e) => set("phone", e.target.value)}
                  />
                </Field>
                <Field label="WebサイトURL">
                  <input
                    className="input"
                    value={form.website_url}
                    onChange={(e) => set("website_url", e.target.value)}
                  />
                </Field>
              </div>
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold text-slate-700">会社紹介文</h2>
              <p className="mt-1 text-xs text-slate-500">
                メール生成時の文脈として AI に渡されます（長い場合は自動でトリムされます）。
              </p>
              <textarea
                className="input mt-3 min-h-[120px]"
                value={form.company_profile}
                onChange={(e) => set("company_profile", e.target.value)}
                placeholder="例：当社は海外の優れたプロダクトを日本市場に紹介し、Makuake / GreenFunding での立ち上げから独占販売までを支援しています。"
              />
            </section>

            <section className="rounded-lg border border-slate-200 bg-white p-5">
              <h2 className="text-sm font-semibold text-slate-700">
                署名テンプレート
              </h2>
              <p className="mt-1 text-xs text-slate-500">
                本文末尾に固定で連結されます。
                <code className="rounded bg-slate-100 px-1">
                  {"{sender_name}"}
                </code>{" "}
                などのプレースホルダが上記の値に置換されます。空欄の場合は既定の署名が使われます。
              </p>
              <textarea
                className="input mt-3 min-h-[200px] font-mono"
                value={form.signature_template}
                onChange={(e) => set("signature_template", e.target.value)}
                placeholder={SIGNATURE_PLACEHOLDER}
              />
            </section>

            <div className="flex items-center gap-3">
              <button
                onClick={onSave}
                disabled={saving}
                className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
              >
                {saving ? "保存中…" : "保存する"}
              </button>
              {savedAt && (
                <span className="text-xs text-green-700">
                  保存しました（{new Date(savedAt).toLocaleString("ja-JP")}）
                </span>
              )}
            </div>
          </div>
        )}
      </div>
    </main>
  );
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
