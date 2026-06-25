"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import Header from "@/components/Header";
import {
  ACTIVITY_KIND_LABELS,
  addActivity,
  addContact,
  CRM_STATUS_LABELS,
  deleteActivity,
  deleteContact,
  deleteMaker,
  fetchMaker,
  formatDateTime,
  updateMaker,
  type ActivityKind,
  type Contact,
  type CrmStatus,
  type MakerDetail,
} from "@/lib/api";

const STATUSES: CrmStatus[] = ["lead", "contacted", "negotiating", "won", "lost"];
const KINDS: ActivityKind[] = ["email", "call", "meeting", "note", "other"];

export default function MakerDetailPage() {
  const params = useParams();
  const id = Number(params.id);

  const [maker, setMaker] = useState<MakerDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  // 編集フォーム状態
  const [form, setForm] = useState({
    name: "",
    website_url: "",
    country: "",
    status: "lead" as CrmStatus,
    next_action: "",
    next_action_date: "",
    notes: "",
  });

  // 担当者追加
  const [c, setC] = useState({ name: "", role: "", email: "", phone: "" });
  // 営業履歴追加
  const [a, setA] = useState({
    kind: "email" as ActivityKind,
    summary: "",
    contact_id: "" as string,
  });

  function reload() {
    fetchMaker(id)
      .then((m) => {
        setMaker(m);
        setForm({
          name: m.name,
          website_url: m.website_url ?? "",
          country: m.country ?? "",
          status: m.status,
          next_action: m.next_action ?? "",
          next_action_date: m.next_action_date ?? "",
          notes: m.notes ?? "",
        });
      })
      .catch((e) => setError(String(e)));
  }

  useEffect(() => {
    reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function onSave() {
    setSaving(true);
    try {
      await updateMaker(id, {
        name: form.name,
        website_url: form.website_url || null,
        country: form.country || null,
        status: form.status,
        next_action: form.next_action || null,
        next_action_date: form.next_action_date || null,
        notes: form.notes || null,
      });
      reload();
    } catch (e) {
      setError(String(e));
    } finally {
      setSaving(false);
    }
  }

  async function onAddContact() {
    if (!c.name.trim()) return;
    try {
      await addContact(id, {
        name: c.name.trim(),
        role: c.role || null,
        email: c.email || null,
        phone: c.phone || null,
      });
      setC({ name: "", role: "", email: "", phone: "" });
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  async function onAddActivity() {
    if (!a.summary.trim()) return;
    try {
      await addActivity(id, {
        kind: a.kind,
        summary: a.summary.trim(),
        contact_id: a.contact_id ? Number(a.contact_id) : null,
      });
      setA({ kind: "email", summary: "", contact_id: "" });
      reload();
    } catch (e) {
      setError(String(e));
    }
  }

  async function onDeleteMaker() {
    if (!window.confirm("このメーカーを削除しますか？（担当者・営業履歴も削除されます）"))
      return;
    try {
      await deleteMaker(id);
      window.location.href = "/crm";
    } catch (e) {
      setError(String(e));
    }
  }

  if (error) {
    return (
      <>
        <Header />
        <main className="mx-auto max-w-3xl px-6 py-8">
          <p className="text-red-600">読み込み失敗：{error}</p>
          <Link href="/crm" className="mt-4 inline-block text-blue-700 hover:underline">
            ← 営業管理へ戻る
          </Link>
        </main>
      </>
    );
  }

  if (!maker) {
    return (
      <>
        <Header />
        <main className="mx-auto max-w-3xl px-6 py-8 text-slate-400">読み込み中…</main>
      </>
    );
  }

  const contactName = (cid: number | null) =>
    maker.contacts.find((x) => x.id === cid)?.name ?? null;

  return (
    <>
      <Header />
      <main className="mx-auto max-w-3xl px-6 py-8">
        <Link href="/crm" className="text-sm text-blue-700 hover:underline">
          ← 営業管理へ戻る
        </Link>

        {/* 基本情報 + 編集 */}
        <div className="mt-4 rounded-lg border border-slate-200 bg-white p-5">
          <div className="flex items-center justify-between">
            <h1 className="text-xl font-bold">{maker.name}</h1>
            <button
              onClick={onDeleteMaker}
              className="text-xs text-red-600 hover:underline"
            >
              削除
            </button>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field label="メーカー名">
              <input
                className="input"
                value={form.name}
                onChange={(e) => setForm({ ...form, name: e.target.value })}
              />
            </Field>
            <Field label="交渉ステータス">
              <select
                className="input"
                value={form.status}
                onChange={(e) =>
                  setForm({ ...form, status: e.target.value as CrmStatus })
                }
              >
                {STATUSES.map((s) => (
                  <option key={s} value={s}>
                    {CRM_STATUS_LABELS[s]}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="公式サイト">
              <input
                className="input"
                value={form.website_url}
                onChange={(e) => setForm({ ...form, website_url: e.target.value })}
              />
            </Field>
            <Field label="国">
              <input
                className="input"
                value={form.country}
                onChange={(e) => setForm({ ...form, country: e.target.value })}
              />
            </Field>
            <Field label="次回アクション">
              <input
                className="input"
                value={form.next_action}
                onChange={(e) => setForm({ ...form, next_action: e.target.value })}
              />
            </Field>
            <Field label="次回アクション期日">
              <input
                type="date"
                className="input"
                value={form.next_action_date}
                onChange={(e) =>
                  setForm({ ...form, next_action_date: e.target.value })
                }
              />
            </Field>
          </div>
          <div className="mt-3">
            <Field label="メモ">
              <textarea
                className="input min-h-[60px]"
                value={form.notes}
                onChange={(e) => setForm({ ...form, notes: e.target.value })}
              />
            </Field>
          </div>
          <div className="mt-3 flex items-center gap-3">
            <button
              onClick={onSave}
              disabled={saving}
              className="rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              {saving ? "保存中…" : "保存"}
            </button>
            {maker.project_ids.length > 0 && (
              <span className="text-xs text-slate-500">
                紐づく案件：
                {maker.project_ids.map((pid) => (
                  <Link
                    key={pid}
                    href={`/projects/${pid}`}
                    className="ml-1 text-blue-700 hover:underline"
                  >
                    #{pid}
                  </Link>
                ))}
              </span>
            )}
          </div>
        </div>

        {/* 担当者 */}
        <section className="mt-8">
          <h2 className="text-sm font-semibold text-slate-700">担当者</h2>
          <div className="mt-2 space-y-2">
            {maker.contacts.map((ct: Contact) => (
              <div
                key={ct.id}
                className="flex items-center justify-between rounded border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <div>
                  <span className="font-medium text-slate-800">{ct.name}</span>
                  {ct.role && <span className="ml-2 text-xs text-slate-400">{ct.role}</span>}
                  <div className="text-xs text-slate-500">
                    {[ct.email, ct.phone].filter(Boolean).join(" / ") || "—"}
                  </div>
                </div>
                <button
                  onClick={async () => {
                    await deleteContact(ct.id);
                    reload();
                  }}
                  className="text-xs text-red-600 hover:underline"
                >
                  削除
                </button>
              </div>
            ))}
            {maker.contacts.length === 0 && (
              <p className="text-sm text-slate-400">担当者は未登録です。</p>
            )}
          </div>
          <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-4">
            <input
              className="input"
              placeholder="氏名"
              value={c.name}
              onChange={(e) => setC({ ...c, name: e.target.value })}
            />
            <input
              className="input"
              placeholder="役職"
              value={c.role}
              onChange={(e) => setC({ ...c, role: e.target.value })}
            />
            <input
              className="input"
              placeholder="メール"
              value={c.email}
              onChange={(e) => setC({ ...c, email: e.target.value })}
            />
            <input
              className="input"
              placeholder="電話"
              value={c.phone}
              onChange={(e) => setC({ ...c, phone: e.target.value })}
            />
          </div>
          <button
            onClick={onAddContact}
            disabled={!c.name.trim()}
            className="mt-2 rounded border border-slate-300 px-3 py-1 text-sm text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            担当者を追加
          </button>
        </section>

        {/* 営業履歴 */}
        <section className="mt-8">
          <h2 className="text-sm font-semibold text-slate-700">営業履歴</h2>

          <div className="mt-3 rounded-lg border border-slate-200 bg-white p-4">
            <div className="flex flex-wrap gap-2">
              <select
                className="input w-32"
                value={a.kind}
                onChange={(e) => setA({ ...a, kind: e.target.value as ActivityKind })}
              >
                {KINDS.map((k) => (
                  <option key={k} value={k}>
                    {ACTIVITY_KIND_LABELS[k]}
                  </option>
                ))}
              </select>
              {maker.contacts.length > 0 && (
                <select
                  className="input w-40"
                  value={a.contact_id}
                  onChange={(e) => setA({ ...a, contact_id: e.target.value })}
                >
                  <option value="">担当者なし</option>
                  {maker.contacts.map((ct) => (
                    <option key={ct.id} value={ct.id}>
                      {ct.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
            <textarea
              className="input mt-2 min-h-[60px] w-full"
              placeholder="内容（例：独占販売の打診メールを送付）"
              value={a.summary}
              onChange={(e) => setA({ ...a, summary: e.target.value })}
            />
            <button
              onClick={onAddActivity}
              disabled={!a.summary.trim()}
              className="mt-2 rounded bg-slate-900 px-4 py-1.5 text-sm font-medium text-white hover:bg-slate-700 disabled:opacity-50"
            >
              履歴を追加
            </button>
          </div>

          <ol className="mt-4 space-y-2">
            {maker.activities.map((act) => (
              <li
                key={act.id}
                className="rounded border border-slate-200 bg-white px-3 py-2 text-sm"
              >
                <div className="flex items-center justify-between">
                  <span className="flex items-center gap-2">
                    <span className="rounded bg-slate-100 px-2 py-0.5 text-xs font-medium text-slate-700">
                      {ACTIVITY_KIND_LABELS[act.kind]}
                    </span>
                    {contactName(act.contact_id) && (
                      <span className="text-xs text-slate-400">
                        {contactName(act.contact_id)}
                      </span>
                    )}
                    {act.project_id && (
                      <Link
                        href={`/projects/${act.project_id}`}
                        className="text-xs text-blue-700 hover:underline"
                      >
                        案件#{act.project_id}
                      </Link>
                    )}
                  </span>
                  <span className="flex items-center gap-2 text-xs text-slate-400">
                    {formatDateTime(act.occurred_at)}
                    <button
                      onClick={async () => {
                        await deleteActivity(act.id);
                        reload();
                      }}
                      className="text-red-600 hover:underline"
                    >
                      削除
                    </button>
                  </span>
                </div>
                <p className="mt-1 whitespace-pre-wrap text-slate-700">{act.summary}</p>
              </li>
            ))}
            {maker.activities.length === 0 && (
              <p className="text-sm text-slate-400">営業履歴はまだありません。</p>
            )}
          </ol>
        </section>
      </main>
    </>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col text-xs text-slate-500">
      {label}
      <span className="mt-1">{children}</span>
    </label>
  );
}
