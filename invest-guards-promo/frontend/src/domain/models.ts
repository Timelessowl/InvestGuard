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
      url: z.string().nullable().default(null),
      // Since structured events; older evidence has none of these.
      kind: z.string().nullable().optional(),
      eventId: z.number().nullable().optional(),
      eventType: z.string().nullable().optional(),
      occurredAt: z.string().nullable().optional(),
    }),
  ),
  // Open set: a new origin from the backend must not break the whole state.
  origin: z.string().default("demo"),
  inn: z.string().nullable().default(null),
  risk: z
    .object({
      verdict: z.enum(["red", "yellow", "green"]),
      inn: z.string(),
      company: z.string().default(""),
      status: z.string().default(""),
      flags: z.array(
        z.object({
          level: z.enum(["red", "yellow", "green"]),
          code: z.string(),
          title: z.string(),
          detail: z.string().default(""),
        }),
      ),
      ratings: z
        .array(z.object({ model: z.string(), rating: z.number(), level: z.string() }))
        .default([]),
      checkedAt: z.string(),
      source: z.string().default("Контур.Фокус"),
    })
    .nullable()
    .default(null),
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
export const sourceStatuses = ["active", "retry", "not_configured", "disabled"] as const;
export type SourceStatus = (typeof sourceStatuses)[number];
export const sourceStatusLabels: Record<SourceStatus, string> = {
  active: "Работает",
  retry: "Нужен повтор",
  not_configured: "Требуется настройка",
  disabled: "Отключён",
};
export const connectionStates = ["ready", "not_configured", "manual_only", "no_access", "error"] as const;
export type Connection = (typeof connectionStates)[number];
export const connectionLabels: Record<Connection, string> = {
  ready: "Подключён",
  not_configured: "Требуется настройка",
  manual_only: "Загрузка файлом",
  no_access: "Нет доступа",
  error: "Ошибка адаптера",
};
export const runStatsSchema = z.object({
  status: z.string(),
  startedAt: z.string(),
  finishedAt: z.string().nullable().default(null),
  fetched: z.number().default(0),
  inserted: z.number().default(0),
  updated: z.number().default(0),
  unchanged: z.number().default(0),
  rejected: z.number().default(0),
  retries: z.number().default(0),
  error: z.string().nullable().default(null),
});
export type RunStats = z.infer<typeof runStatsSchema>;
// Job sources and hiring analytics; absent for news sources and older backends.
export const hiringStatsSchema = z.object({
  vacancies: z.number().default(0),
  activeVacancies: z.number().default(0),
  employers: z.number().default(0),
  signals: z.number().default(0),
  activeSignals: z.number().default(0),
});
export type HiringStats = z.infer<typeof hiringStatsSchema>;
export const sourceSchema = z.object({
  id: z.string(),
  name: z.string(),
  category: z.string(),
  url: z.string().default(""),
  // Open set of kinds; unknown statuses fall back to "needs attention" instead of failing the state.
  kind: z.string().default("rss"),
  group: z.string().default("news"),
  produces: z.enum(["documents", "events"]).catch("documents"),
  schedule: z.string(),
  documents: z.number(),
  signals: z.number().default(0),
  events: z.number().default(0),
  eventSignals: z.number().default(0),
  errors7d: z.number().default(0),
  connection: z.enum(connectionStates).default("ready").catch("error"),
  dataType: z.string().nullable().default(null),
  access: z.string().nullable().default(null),
  docsUrl: z.string().nullable().default(null),
  lastRunStats: runStatsSchema.nullable().default(null),
  lastCheck: z
    .object({ ok: z.boolean().nullable(), message: z.string().nullable(), checkedAt: z.string() })
    .nullable()
    .default(null),
  hiring: hiringStatsSchema.nullable().default(null),
  lastRun: z.string(),
  lastError: z.string().nullable().default(null),
  status: z.enum(sourceStatuses).catch("retry"),
  share: z.number(),
});
export type DataSource = z.infer<typeof sourceSchema>;
export const collectResultSchema = z.object({
  source: sourceSchema,
  added: z.number(),
  queued: z.boolean().default(false),
});
export type CollectResult = z.infer<typeof collectResultSchema>;
export const sourceCheckSchema = z.object({
  ok: z.boolean(),
  message: z.string(),
  checkedAt: z.string(),
  connection: z.enum(connectionStates).catch("error"),
});
export type SourceCheck = z.infer<typeof sourceCheckSchema>;
export const sourceRunSchema = runStatsSchema.extend({
  id: z.number(),
  sourceId: z.string(),
  trigger: z.string(),
});
export type SourceRun = z.infer<typeof sourceRunSchema>;

export const processingStatuses = ["new", "linked", "waiting", "review", "context", "aggregated", "ignored", "error"] as const;
export type ProcessingStatus = (typeof processingStatuses)[number];
export const processingLabels: Record<ProcessingStatus, string> = {
  new: "Ожидает разбора",
  linked: "Связано с карточкой",
  waiting: "Ждёт новых признаков",
  review: "Нужна проверка",
  context: "Контекст",
  aggregated: "В агрегированных сигналах",
  ignored: "Скрыто",
  error: "Ошибка",
};
export const eventTypeSchema = z.object({
  code: z.string(),
  title: z.string(),
  category: z.string(),
  polarity: z.string(),
  linkPolicy: z.string(),
  stage: z.string().nullable().default(null),
  description: z.string().default(""),
  deprecated: z.boolean().default(false),
});
export type EventType = z.infer<typeof eventTypeSchema>;
export const eventSchema = z.object({
  id: z.number(),
  sourceId: z.string(),
  externalId: z.string().nullable(),
  type: z.string(),
  typeTitle: z.string(),
  category: z.string(),
  title: z.string(),
  companyName: z.string().nullable(),
  inn: z.string().nullable(),
  ogrn: z.string().nullable(),
  municipality: z.string().nullable(),
  address: z.string().nullable(),
  cadastralNumber: z.string().nullable(),
  latitude: z.number().nullable(),
  longitude: z.number().nullable(),
  locationPrecision: z.string().nullable(),
  eventDate: z.string().nullable(),
  status: z.string().nullable(),
  value: z.number().nullable(),
  valueUnit: z.string().nullable(),
  metrics: z.array(z.record(z.string(), z.unknown())).default([]),
  url: z.string().nullable(),
  collectedAt: z.string(),
  updatedAt: z.string(),
  lastSeenAt: z.string(),
  processingStatus: z.enum(processingStatuses).catch("new"),
  processingReason: z.string().nullable(),
  candidates: z.array(z.object({ id: z.string(), confidence: z.string() })).default([]),
});
export type SourceEvent = z.infer<typeof eventSchema>;
export const eventPageSchema = z.object({
  items: eventSchema.array(),
  total: z.number(),
  limit: z.number(),
  offset: z.number(),
});
export type EventPage = z.infer<typeof eventPageSchema>;
export const eventDetailSchema = eventSchema.extend({
  sourceCompanyId: z.string().nullable(),
  raw: z.record(z.string(), z.unknown()),
  duplicateOf: z.number().nullable(),
  links: z.array(
    z.object({
      id: z.string(),
      confidence: z.string(),
      company: z.string(),
      linkedBy: z.string(),
      reason: z.string().nullable(),
      createdAt: z.string(),
    }),
  ),
  versions: z.array(
    z.object({
      replacedAt: z.string(),
      contentHash: z.string(),
      status: z.string().nullable(),
      fields: z.record(z.string(), z.unknown()),
      raw: z.record(z.string(), z.unknown()),
    }),
  ),
});
export type SourceEventDetail = z.infer<typeof eventDetailSchema>;
export interface EventFilters {
  type?: string[];
  inn?: string;
  company?: string;
  municipality?: string;
  source?: string;
  processing?: ProcessingStatus;
  candidate?: string;
  dateFrom?: string;
  dateTo?: string;
  limit?: number;
  offset?: number;
}

export const pilotAnalyticsSchema = z.object({
  confirmationRate: z.string(),
  medianReview: z.string(),
  leadTime: z.string(),
  signals: z.array(z.object({ label: z.string(), value: z.number() })),
  sourceSignals: z.number().int().nonnegative(),
});
export type PilotAnalytics = z.infer<typeof pilotAnalyticsSchema>;
export const documentStages = [
  "new",
  "skipped",
  "rejected",
  "signal",
  "merged",
  "error",
] as const;
export type DocumentStage = (typeof documentStages)[number];
export const documentStageLabels: Record<DocumentStage, string> = {
  new: "В очереди",
  skipped: "Отсеян фильтром",
  rejected: "Отклонён ИИ",
  signal: "Новая карточка",
  merged: "Добавлен к карточке",
  error: "Ошибка",
};
export const llmUsageSchema = z.object({
  spentUsd: z.number(),
  todayUsd: z.number(),
  budgetUsd: z.number(),
  dailyBudgetUsd: z.number(),
  calls: z.number(),
  byPurpose: z.record(z.string(), z.number()),
  configured: z.boolean(),
});
export type LlmUsage = z.infer<typeof llmUsageSchema>;
export const pipelineSchema = z.object({
  stages: z.record(z.string(), z.number()),
  usage: llmUsageSchema,
  focus: z
    .object({ configured: z.boolean(), used: z.number(), limit: z.number() })
    .nullable()
    .default(null),
});
export type PipelineStats = z.infer<typeof pipelineSchema>;
export const documentSchema = z.object({
  id: z.number(),
  sourceId: z.string(),
  url: z.string(),
  title: z.string(),
  publishedAt: z.string().nullable(),
  collectedAt: z.string(),
  stage: z.enum(documentStages),
  reason: z.string().nullable(),
  candidateId: z.string().nullable(),
});
export type CollectedDocument = z.infer<typeof documentSchema>;
export const processResultSchema = z.object({
  processed: z.number(),
  signal: z.number(),
  merged: z.number(),
  rejected: z.number(),
  skipped: z.number(),
  error: z.number(),
  busy: z.boolean().optional(),
  stopped: z.string().optional(),
});
export type ProcessResult = z.infer<typeof processResultSchema>;
export type ChatMessage = { role: "user" | "assistant"; content: string };
export const chatReplySchema = z.object({
  reply: z.string(),
  trace: z.array(
    z.object({
      name: z.string(),
      arguments: z.record(z.string(), z.unknown()),
      summary: z.string(),
    }),
  ),
  changed: z.boolean(),
});
export type ChatReply = z.infer<typeof chatReplySchema>;
export const stateSchema = z.object({
  candidates: candidateSchema.array(),
  sources: sourceSchema.array(),
  pilotAnalytics: pilotAnalyticsSchema,
  pipeline: pipelineSchema,
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
export type RiskReport = NonNullable<Candidate["risk"]>;
export const riskLabels: Record<RiskReport["verdict"], string> = {
  red: "Красная зона",
  yellow: "Жёлтая зона",
  green: "Зелёная зона",
};
