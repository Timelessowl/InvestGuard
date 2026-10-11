import type { Candidate, CandidateStatus } from "./models";

export type SortOrder = "score-desc" | "score-asc" | "company";
export interface QueueFilters {
  query: string;
  status: CandidateStatus | "all" | "pending";
  sector: string;
  sort: SortOrder;
  urgent?: boolean;
}
export function filterCandidates(
  candidates: Candidate[],
  filters: QueueFilters,
) {
  const query = filters.query.trim().toLocaleLowerCase("ru");
  return candidates
    .filter(
      (c) =>
        (!query ||
          [
            c.id,
            c.company,
            c.signal,
            c.signalType,
            c.city,
            c.sector,
            c.source,
          ].some((value) => value.toLocaleLowerCase("ru").includes(query))) &&
        (filters.status === "all" ||
          (filters.status === "pending"
            ? c.status === "new" || c.status === "review"
            : c.status === filters.status)) &&
        (!filters.urgent || c.urgency === "high") &&
        (filters.sector === "all" || c.sector === filters.sector),
    )
    .sort((a, b) =>
      filters.sort === "company"
        ? a.company.localeCompare(b.company, "ru")
        : filters.sort === "score-asc"
          ? a.score - b.score
          : b.score - a.score,
    );
}
export function queueMetrics(candidates: Candidate[]) {
  return {
    open: candidates.filter((c) => c.status === "new" || c.status === "review")
      .length,
    urgent: candidates.filter(
      (c) =>
        c.urgency === "high" && (c.status === "new" || c.status === "review"),
    ).length,
    approved: candidates.filter((c) => c.status === "approved").length,
    exported: candidates.filter((c) => c.status === "exported").length,
    rejected: candidates.filter((c) => c.status === "rejected").length,
  };
}
export function projectCount(count: number) {
  const mod100 = count % 100;
  const mod10 = count % 10;
  const word =
    mod100 >= 11 && mod100 <= 14
      ? "проектов"
      : mod10 === 1
        ? "проект"
        : mod10 >= 2 && mod10 <= 4
          ? "проекта"
          : "проектов";
  return `${count} ${word}`;
}
// Spreadsheet apps interpret leading operators as formulas even inside quoted CSV cells.
export function csvCell(value: string | number) {
  const text = String(value);
  const safe = /^[\s\u0000-\u001f]*[=+@-]/u.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}
export function candidatesToCsv(candidates: Candidate[]) {
  const rows: (string | number)[][] = [
    [
      "ID",
      "Компания",
      "ИНН",
      "Отрасль",
      "Муниципалитет",
      "Сигнал",
      "Стадия",
      "Инвестиции",
      "Приоритет",
      "Ответственный",
      "Следующее действие",
    ],
  ];
  rows.push(
    ...candidates.map((c) => [
      c.id,
      c.company,
      "",
      c.sector,
      c.city,
      c.signal,
      c.stage,
      c.amount,
      c.score,
      c.owner,
      c.nextAction,
    ]),
  );
  return "\ufeff" + rows.map((row) => row.map(csvCell).join(";")).join("\r\n");
}
