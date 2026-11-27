import { describe, expect, it } from "vitest";
import { eventDetailSchema, eventPageSchema, stateSchema } from "./models";
import { isWorkingSource } from "./selectors";
import { eventQuery } from "../services/repository";

const card = {
  id: "INV-0254",
  company: "Ревдинские приводы",
  initials: "РП",
  sector: "Машиностроение",
  city: "Ревда",
  signalType: "Модернизация",
  signal: "Новый цех",
  source: "СМИ",
  sourceDate: "29 окт, 10:00",
  score: 80,
  urgency: "high",
  urgencyLabel: "Высокая",
  status: "new",
  owner: "Не назначен",
  amount: "Не указано",
  stage: "Анонс",
  summary: "",
  factors: {},
  recommendation: "",
  rationale: "",
  evidence: [{ source: "СМИ", date: "29 окт", excerpt: "Текст" }],
};
const oldSource = {
  id: "oblgazeta",
  name: "Областная газета",
  category: "Региональные СМИ",
  url: "https://www.oblgazeta.ru/rss/",
  kind: "rss",
  schedule: "Каждый час",
  documents: 3,
  signals: 1,
  lastRun: "24 ноя, 10:00",
  lastError: null,
  status: "active",
  share: 100,
};
const pilot = { confirmationRate: "41%", medianReview: "1 день", leadTime: "2 мес", signals: [], sourceSignals: 0 };
const pipeline = {
  stages: {},
  usage: { spentUsd: 0, todayUsd: 0, budgetUsd: 45, dailyBudgetUsd: 3, calls: 0, byPurpose: {}, configured: false },
};

describe("/api/state compatibility", () => {
  it("accepts the state of the backend before structured events", () => {
    const state = stateSchema.parse({ candidates: [card], sources: [oldSource], pilotAnalytics: pilot, pipeline });
    expect(state.sources[0]).toMatchObject({ connection: "ready", produces: "documents", events: 0, lastRunStats: null });
    expect(isWorkingSource(state.sources[0])).toBe(true);
    expect(state.candidates[0].evidence[0].eventId).toBeUndefined();
  });
  it("accepts new source kinds, statuses, origins and event evidence", () => {
    const eventSource = {
      ...oldSource,
      id: "trudvsem",
      kind: "trudvsem",
      group: "hiring",
      produces: "events",
      connection: "not_configured",
      status: "not_configured",
      events: 0,
      lastRunStats: { status: "not_configured", startedAt: "2026-11-24T10:00:00+00:00", error: "Требуется настройка" },
    };
    const future = { ...oldSource, id: "future", kind: "satellite", produces: "pictures", connection: "quantum", status: "warming_up" };
    const eventCard = {
      ...card,
      id: "INV-1001",
      origin: "events",
      evidence: [{ source: "Россети · Заявка", date: "24 ноя 2026", excerpt: "2 МВт", url: null, kind: "event", eventId: 5,
        eventType: "engineering_connection", occurredAt: "2026-11-24" }],
    };
    const state = stateSchema.parse({ candidates: [card, eventCard], sources: [oldSource, eventSource, future], pilotAnalytics: pilot, pipeline });
    expect(state.sources[1]).toMatchObject({ kind: "trudvsem", status: "not_configured", produces: "events" });
    expect(isWorkingSource(state.sources[1])).toBe(false);
    expect(state.sources[2]).toMatchObject({ kind: "satellite", produces: "documents", connection: "error", status: "retry" });
    expect(isWorkingSource(state.sources[2])).toBe(false);
    expect(state.candidates[1].origin).toBe("events");
    expect(state.candidates[1].evidence[0].eventId).toBe(5);
  });
  it("accepts hiring stats of job sources and keeps them optional", () => {
    const jobs = {
      ...oldSource,
      id: "trudvsem",
      kind: "trudvsem",
      group: "hiring",
      produces: "events",
      hiring: { vacancies: 16431, activeVacancies: 15800, employers: 2900, signals: 12, activeSignals: 9 },
    };
    const partial = { ...oldSource, id: "hiring-signals", kind: "derived", produces: "events", hiring: { signals: 3 } };
    const state = stateSchema.parse({ candidates: [card], sources: [oldSource, jobs, partial], pilotAnalytics: pilot, pipeline });
    expect(state.sources[0].hiring).toBeNull();
    expect(state.sources[1].hiring).toEqual({ vacancies: 16431, activeVacancies: 15800, employers: 2900, signals: 12, activeSignals: 9 });
    expect(state.sources[2].hiring).toMatchObject({ signals: 3, activeSignals: 0, vacancies: 0 });
  });
  it("parses event pages and details", () => {
    const event = {
      id: 1, sourceId: "rosseti", externalId: "r-1", type: "engineering_connection", typeTitle: "Заявка на техприсоединение",
      category: "infrastructure", title: "Заявка на 2 МВт", companyName: "АО «Тест»", inn: "6661005707", ogrn: null,
      municipality: "Ревда", address: null, cadastralNumber: null, latitude: null, longitude: null, locationPrecision: "municipality",
      eventDate: "2026-11-01", status: null, value: 2000, valueUnit: "кВт", metrics: [{ name: "work_places", value: 3 }],
      url: null, collectedAt: "2026-11-24T10:00:00+00:00", updatedAt: "2026-11-24T10:00:00+00:00", lastSeenAt: "2026-11-24T10:00:00+00:00",
      processingStatus: "linked", processingReason: null, candidates: [{ id: "INV-0254", confidence: "confirmed" }],
    };
    expect(eventPageSchema.parse({ items: [event], total: 1, limit: 50, offset: 0 }).items[0].candidates[0].id).toBe("INV-0254");
    const detail = eventDetailSchema.parse({ ...event, sourceCompanyId: null, raw: { id: "r-1" }, duplicateOf: null, links: [],
      versions: [{ replacedAt: "2026-11-24T11:00:00+00:00", contentHash: "abc", status: null, fields: {}, raw: {} }] });
    expect(detail.raw).toEqual({ id: "r-1" });
  });
  it("builds event filter queries", () => {
    expect(eventQuery({})).toBe("");
    expect(eventQuery({ type: ["vacancy_growth", "construction_permit"], inn: "6661005707", municipality: "Ревда", limit: 20 }))
      .toBe("?type=vacancy_growth&type=construction_permit&inn=6661005707&municipality=%D0%A0%D0%B5%D0%B2%D0%B4%D0%B0&limit=20");
  });
});
