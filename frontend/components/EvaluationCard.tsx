import RecBadge from "@/components/RecBadge";
import {
  formatDateTime,
  ULULE_AXIS_LABELS,
  type Evaluation,
} from "@/lib/api";

export default function EvaluationCard({ ev }: { ev: Evaluation }) {
  // 標準軸と Ulule 専用軸（英語キー）を分離して表示する
  const entries = Object.entries(ev.axis_scores);
  const standard = entries.filter(([k]) => !(k in ULULE_AXIS_LABELS));
  const ulule = entries.filter(([k]) => k in ULULE_AXIS_LABELS);

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

      {/* 軸別スコア（標準） */}
      <div className="mt-4 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
        {standard.map(([axis, score]) => (
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

      {/* Ulule 適性スコア（Ulule 案件のみ表示） */}
      {ulule.length > 0 && (
        <div className="mt-4 rounded-md border border-purple-200 bg-purple-50 p-3">
          <p className="text-xs font-semibold text-purple-800">
            Ulule 適性スコア（欧州デザイン / サステナブル / ギフト性 等）
          </p>
          <div className="mt-2 grid grid-cols-1 gap-1.5 sm:grid-cols-2">
            {ulule.map(([axis, score]) => (
              <div key={axis} className="flex items-center gap-2 text-xs">
                <span className="w-36 shrink-0 text-purple-700">
                  {ULULE_AXIS_LABELS[axis] ?? axis}
                </span>
                <div className="h-2 flex-1 rounded bg-purple-100">
                  <div
                    className="h-2 rounded bg-purple-600"
                    style={{ width: `${Math.min(100, Math.max(0, score))}%` }}
                  />
                </div>
                <span className="w-8 text-right text-purple-700">{score}</span>
              </div>
            ))}
          </div>
        </div>
      )}

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
