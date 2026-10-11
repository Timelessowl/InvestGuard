import type { Candidate, ProjectLocation } from "./models";

export type Priority = "high" | "medium" | "low";
export const priorities: { id: Priority; label: string; range: string }[] = [
  { id: "high", label: "Высокий", range: "80–100" },
  { id: "medium", label: "Средний", range: "60–79" },
  { id: "low", label: "Низкий", range: "0–59" },
];
export function projectPriority(score: number): Priority {
  return score >= 80 ? "high" : score >= 60 ? "medium" : "low";
}

export function projectLocation(candidate: Candidate): ProjectLocation | null {
  return candidate.location;
}
export function projectsOnMap(
  candidates: Candidate[],
  priority: Priority | "all" = "all",
) {
  return candidates.flatMap((candidate) => {
    const location = projectLocation(candidate);
    return location &&
      (priority === "all" || projectPriority(candidate.score) === priority)
      ? [{ candidate, location }]
      : [];
  });
}
