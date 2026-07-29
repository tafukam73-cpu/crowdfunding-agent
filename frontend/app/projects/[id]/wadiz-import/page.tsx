"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import WadizImportPanel from "@/components/WadizImportPanel";
import { fetchProject, siteLabel, type Project } from "@/lib/api";

export default function WadizImportPage({
  params,
}: {
  params: { id: string };
}) {
  const id = Number(params.id);
  const [project, setProject] = useState<Project | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    fetchProject(id)
      .then((p) => active && setProject(p))
      .catch((e) => active && setError(String(e)));
    return () => {
      active = false;
    };
  }, [id]);

  return (
    <div className="min-h-screen bg-slate-50">
      <main className="mx-auto max-w-3xl px-6 py-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900">
              Wadiz公開情報の取り込み
            </h1>
            {project && (
              <p className="text-sm text-slate-500">
                {siteLabel(project.source_site)}：{project.title}
              </p>
            )}
          </div>
          <Link
            href={`/projects/${id}`}
            className="rounded border border-slate-300 px-3 py-1.5 text-sm text-slate-700 hover:bg-white"
          >
            ← 案件詳細へ戻る
          </Link>
        </div>

        {error && (
          <div className="rounded border border-rose-200 bg-rose-50 p-3 text-sm text-rose-700">
            案件の読み込みに失敗しました：{error}
          </div>
        )}

        {project && project.source_site !== "wadiz" && (
          <div className="rounded border border-amber-200 bg-amber-50 p-3 text-sm text-amber-800">
            この案件は Wadiz ではありません（手動取り込みは Wadiz 案件向けです）。
          </div>
        )}

        {project && (
          <WadizImportPanel
            projectId={id}
            defaultSourceUrl={project.source_url}
          />
        )}
        {!project && !error && (
          <div className="rounded border border-slate-200 bg-white p-8 text-center text-slate-400">
            読み込み中…
          </div>
        )}
      </main>
    </div>
  );
}
