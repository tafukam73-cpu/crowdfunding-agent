import { SITE_COLORS, SITE_LABELS, type SourceSite } from "@/lib/api";

export default function SourceBadge({ site }: { site: SourceSite }) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${SITE_COLORS[site]}`}
    >
      {SITE_LABELS[site]}
    </span>
  );
}
