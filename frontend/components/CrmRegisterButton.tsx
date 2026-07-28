"use client";

import Link from "next/link";
import { useState } from "react";

import { createMakerFromProject } from "@/lib/api";

// 「CRMに登録」ボタン。案件ランキング / 案件一覧 / 案件詳細で共通利用する。
// - source="project" : 海外案件（Project）から登録
// 二重登録はバックエンドが website_url / 会社名で防止する（冪等）。
// 既存メーカー再利用時は「すでにCRM登録済みです」を表示する。
type Props = {
  source: "project";
  id: number;
  // Project で既に maker_id がリンク済みなら渡す（初期状態で「登録済み」を表示）。
  initialMakerId?: number | null;
  size?: "sm" | "md";
  className?: string;
};

type Phase = "idle" | "loading" | "created" | "exists" | "error";

export default function CrmRegisterButton({
  id,
  initialMakerId = null,
  size = "sm",
  className = "",
}: Props) {
  const [makerId, setMakerId] = useState<number | null>(initialMakerId);
  // 初期リンク済みなら最初から「登録済み」状態にする。
  const [phase, setPhase] = useState<Phase>(
    initialMakerId != null ? "exists" : "idle"
  );

  const btnSize =
    size === "md" ? "px-3 py-1.5 text-sm" : "px-2.5 py-1 text-xs";

  async function register() {
    setPhase("loading");
    try {
      const result = await createMakerFromProject(id);
      setMakerId(result.maker.id);
      setPhase(result.created ? "created" : "exists");
    } catch {
      setPhase("error");
    }
  }

  // 登録済み（新規 or 既存）: メッセージ + メーカー詳細リンク
  if (makerId != null && (phase === "created" || phase === "exists")) {
    const msg = phase === "created" ? "CRMに登録しました" : "すでにCRM登録済みです";
    return (
      <span className={`inline-flex flex-wrap items-center gap-2 ${className}`}>
        <span className="inline-flex items-center gap-1 rounded bg-emerald-100 px-2 py-0.5 text-xs font-medium text-emerald-700">
          ✓ {msg}
        </span>
        <Link
          href={`/crm/makers/${makerId}`}
          className="text-xs font-medium text-blue-700 hover:underline"
        >
          メーカー詳細 →
        </Link>
      </span>
    );
  }

  return (
    <span className={`inline-flex flex-wrap items-center gap-2 ${className}`}>
      <button
        onClick={register}
        disabled={phase === "loading"}
        className={`rounded border border-emerald-600 bg-emerald-600 font-medium text-white hover:bg-emerald-700 disabled:opacity-50 ${btnSize}`}
      >
        {phase === "loading" ? "登録中…" : "CRMに登録"}
      </button>
      {phase === "error" && (
        <span className="text-xs font-medium text-red-600">
          CRM登録に失敗しました
        </span>
      )}
    </span>
  );
}
