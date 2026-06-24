import { REC_COLORS, REC_LABELS, type Recommendation } from "@/lib/api";

export default function RecBadge({
  recommendation,
}: {
  recommendation: Recommendation | null;
}) {
  if (!recommendation) {
    return <span className="text-xs text-slate-300">未評価</span>;
  }
  return (
    <span
      className={`inline-block rounded-full px-2 py-0.5 text-xs font-medium ${REC_COLORS[recommendation]}`}
    >
      推奨{REC_LABELS[recommendation]}
    </span>
  );
}
