import { describe, expect, it } from "vitest";
import { candidateSchema, type Candidate } from "./models";
import { filterCandidates, projectCount, queueMetrics } from "./selectors";
import { projectPriority, projectsOnMap } from "./geography";

const base = {
  initials: "АА",
  sector: "Металлургия",
  signalType: "Модернизация",
  source: "СМИ",
  sourceDate: "29 окт, 10:00",
  urgency: "high",
  urgencyLabel: "Высокая",
  owner: "Не назначен",
  amount: "Не указано",
  stage: "Анонс",
  summary: "",
  factors: {},
  recommendation: "",
  rationale: "",
  evidence: [{ source: "СМИ", date: "29 окт", excerpt: "Текст" }],
};
const make = (fields: Partial<Candidate>) => candidateSchema.parse({ ...base, ...fields });
const candidates = [
  make({ id: "INV-1", company: "Альфа", city: "Ревда", signal: "Новый цех", score: 85, status: "new", origin: "ai" }),
  make({ id: "INV-2", company: "Бета", city: "Ирбит", signal: "Склад", score: 55, status: "approved", location: null }),
  make({
    id: "INV-3",
    company: "Гамма",
    city: "Екатеринбург",
    signal: "Завод",
    score: 70,
    status: "review",
    location: { latitude: 56.8, longitude: 60.6, precision: "municipality" },
  }),
];

describe("selectors", () => {
  it("defaults optional API fields for older payloads", () => {
    expect(candidates[1].origin).toBe("demo");
    expect(candidates[0].evidence[0].url).toBeNull();
  });
  it("filters pending candidates by query and sorts by score", () => {
    const result = filterCandidates(candidates, { query: "", status: "pending", sector: "all", sort: "score-asc" });
    expect(result.map((c) => c.id)).toEqual(["INV-3", "INV-1"]);
    expect(filterCandidates(candidates, { query: "ирбит", status: "all", sector: "all", sort: "score-desc" })).toHaveLength(1);
  });
  it("counts queue metrics and pluralises", () => {
    expect(queueMetrics(candidates)).toMatchObject({ open: 2, urgent: 2, approved: 1 });
    expect([projectCount(1), projectCount(3), projectCount(11)]).toEqual(["1 проект", "3 проекта", "11 проектов"]);
  });
  it("puts only located candidates on the map by priority", () => {
    expect(projectPriority(80)).toBe("high");
    expect(projectsOnMap(candidates).map((p) => p.candidate.id)).toEqual(["INV-3"]);
    expect(projectsOnMap(candidates, "high")).toHaveLength(0);
  });
});
