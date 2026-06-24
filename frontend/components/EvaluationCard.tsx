import RecBadge from "@/components/RecBadge";
import { formatDateTime, type Evaluation } from "@/lib/api";

export default function EvaluationCard({ ev }: { ev: Evaluation }) {
  return (
    <div className="rounded-lg border border-slate-200 bg-white p-5">
      <div className="flex items-center gap-3">
        <span className="text-3xl font-bold text-slate-900">{ev.total_score}</span>
        <span className="text-sm text-slate-400">/ 100</span>
        <RecBadge recommendation={ev.recommendation} />
        <span className="ml-auto text-xs text-slate-400">
          {ev.model} ・ {formatDateTime(ev.created_at)}
        </span>
      </div>

      {/* 軸別スコア */}
      <div className="mt-4 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {Object.entries(ev.axis_scores).map(([axis, score]) => (
          <div key={axis} className="flex items-center gap-2 text-xs">
            <span className="w-32 shrink-0 text-slate-500">{axis}</span>
            <div className="h-2 flex-1 rounded bg-slate-100">
              <div
                className="h-2 rounded bg-slate-700"
                style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
              />
            </div>
            <span className="w-8 text-right text-slate-600">{score}</span>
          </div>
        ))}
      </div>

      {/* テキスト */}
      <dl className="mt-4 space-y-3 text-sm">
        {ev.reasons && (
          <div>
            <dt className="font-semibold text-slate-600">評価理由</dt>
            <dd className="mt-1 whitespace-pre-wrap text-slate-700">{ev.reasons}</dd>
          </div>
        )}
        {ev.concerns && (
          <div>
            <dt className="font-semibold text-slate-600">懸念点</dt>
            <dd className="mt-1 whitespace-pre-wrap text-slate-700">{ev.concerns}</dd>
          </div>
        )}
        {ev.sales_comment && (
          <div>
            <dt className="font-semibold text-slate-600">営業推奨コメント</dt>
            <dd className="mt-1 whitespace-pre-wrap text-slate-700">
              {ev.sales_comment}
            </dd>
          </div>
        )}
      </dl>
    </div>
  );
}
