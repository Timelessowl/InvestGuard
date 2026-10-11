import { z } from "zod";

export const statusSchema = z.enum([
  "new",
  "review",
  "approved",
  "exported",
  "rejected",
]);
export type CandidateStatus = z.infer<typeof statusSchema>;
export type Decision = Extract<
  CandidateStatus,
  "review" | "approved" | "rejected"
>;
export const owners = [
  "Не назначен",
  "А. Крылова",
  "И. Титов",
  "М. Орлова",
] as const;
export const nextActions = [
  "Связаться с компанией",
  "Запросить данные",
  "Передать эксперту",
  "Продолжить мониторинг",
] as const;
export const projectLocationSchema = z.object({
  latitude: z.number().min(-90).max(90),
  longitude: z.number().min(-180).max(180),
  precision: z.enum(["municipality", "project"]),
});
export type ProjectLocation = z.infer<typeof projectLocationSchema>;
export const candidateSchema = z.object({
  id: z.string().min(1),
  company: z.string().min(1),
  initials: z.string(),
  sector: z.string().min(1),
  city: z.string().min(1),
  location: projectLocationSchema.nullable().default(null),
  signalType: z.string(),
  signal: z.string().min(1),
  source: z.string(),
  sourceDate: z.string(),
  score: z.number().min(0).max(100),
  urgency: z.enum(["high", "medium", "low"]),
  urgencyLabel: z.string(),
  status: statusSchema,
  owner: z.string(),
  amount: z.string(),
  stage: z.string(),
  summary: z.string(),
  factors: z.record(z.string(), z.number().min(0).max(100)),
  recommendation: z.string(),
  rationale: z.string(),
  nextAction: z.enum(nextActions).default("Связаться с компанией"),
  evidence: z.array(
    z.object({
      source: z.string(),
      date: z.string(),
      excerpt: z.string(),
      highlights: z.array(z.string()).default([]),
    }),
  ),
});
export type Candidate = z.infer<typeof candidateSchema>;
export const newSignalSchema = z.object({
  company: z
    .string()
    .trim()
    .min(2, "Укажите название компании, минимум 2 символа.")
    .max(160),
  sector: z.string().trim().min(1, "Выберите отрасль."),
  city: z
    .string()
    .trim()
    .min(2, "Укажите муниципалитет, минимум 2 символа.")
    .max(120),
  signal: z
    .string()
    .trim()
    .min(10, "Опишите сигнал подробнее, минимум 10 символов.")
    .max(4000),
  source: z
    .string()
    .trim()
    .min(3, "Укажите название источника или ссылку.")
    .max(1000),
});
export type NewSignal = z.infer<typeof newSignalSchema>;
export const sourceSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.string(),
  schedule: z.string(),
  documents: z.number(),
  lastRun: z.string(),
  status: z.enum(["active", "retry"]),
  share: z.number(),
});
export type DataSource = z.infer<typeof sourceSchema>;

export const pilotAnalyticsSchema = z.object({
  confirmationRate: z.string(),
  medianReview: z.string(),
  leadTime: z.string(),
  signals: z.array(z.object({ label: z.string(), value: z.number() })),
  sourceSignals: z.number().int().nonnegative(),
});
export type PilotAnalytics = z.infer<typeof pilotAnalyticsSchema>;
export const stateSchema = z.object({
  candidates: candidateSchema.array(),
  sources: sourceSchema.array(),
  pilotAnalytics: pilotAnalyticsSchema,
});
export type WorkspaceState = z.infer<typeof stateSchema>;

export const statusLabels: Record<CandidateStatus, string> = {
  new: "Новый",
  review: "На проверке",
  approved: "Одобрен",
  exported: "Экспортирован",
  rejected: "Отклонён",
};
export const sectorOptions = [
  "Композитные материалы",
  "Машиностроение",
  "Металлургия",
  "Химическая промышленность",
  "Логистика",
  "Пищевая промышленность",
  "Строительные материалы",
];
