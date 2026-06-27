import { siteColor, siteLabel, type SourceSite } from "@/lib/api";

export default function SourceBadge({ site }: { site: SourceSite | string }) {
  return (
    <span
      className={`inline-block rounded px-2 py-0.5 text-xs font-medium ${siteColor(site)}`}
    >
      {siteLabel(site)}
    </span>
  );
}
