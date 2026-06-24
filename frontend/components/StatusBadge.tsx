import { STATUS_COLORS, STATUS_LABELS, type ProjectStatus } from "@/lib/api";

export default function StatusBadge({ status }: { status: ProjectStatus }) {
  return (
    <span
      className={`inline-block rounded-full px-2.5 py-0.5 text-xs font-medium ${STATUS_COLORS[status]}`}
    >
      {STATUS_LABELS[status]}
    </span>
  );
}
