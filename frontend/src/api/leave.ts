// Deterministic color per leave type — known types get an intentional hue,
// anything unrecognized still gets a consistent (not random-per-render) color.
// Shared by BalancesPage and TimeOffRequestsPage so the same leave type
// always renders the same color everywhere in the app.
export function typeBadgeClass(name: string): string {
  const key = name.trim().toLowerCase();
  if (key.includes("paid time off") || key === "pto") return "badge-leave-pto";
  if (key.includes("sick")) return "badge-leave-sick";
  if (key.includes("work from home") || key === "wfh") return "badge-leave-wfh";
  if (key.includes("unpaid")) return "badge-leave-unpaid";
  if (key.includes("parental") || key.includes("maternity") || key.includes("paternity")) {
    return "badge-leave-parental";
  }

  const palette = [
    "badge-leave-pto",
    "badge-leave-sick",
    "badge-leave-wfh",
    "badge-leave-unpaid",
    "badge-leave-parental",
    "badge-leave-other",
  ];
  let hash = 0;
  for (let i = 0; i < key.length; i++) hash = (hash * 31 + key.charCodeAt(i)) >>> 0;
  return palette[hash % palette.length];
}