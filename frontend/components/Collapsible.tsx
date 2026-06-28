"use client";

import type { ReactNode } from "react";

// 営業フローで今すぐ必要ない詳細情報を折りたたむ共通コンポーネント。
// ネイティブ <details> を使い、状態管理なしで開閉できる（既定は閉じる）。
export default function Collapsible({
  title,
  children,
  defaultOpen = false,
  hint,
}: {
  title: string;
  children: ReactNode;
  defaultOpen?: boolean;
  hint?: string;
}) {
  return (
    <details
      open={defaultOpen}
      className="group mt-3 rounded-lg border border-slate-200 bg-white"
    >
      <summary className="flex cursor-pointer list-none items-center justify-between gap-2 px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50">
        <span className="flex items-center gap-2">
          <span className="text-slate-400 transition group-open:rotate-90">▶</span>
          {title}
        </span>
        {hint && <span className="text-xs font-normal text-slate-400">{hint}</span>}
      </summary>
      <div className="border-t border-slate-100 px-4 py-4">{children}</div>
    </details>
  );
}
