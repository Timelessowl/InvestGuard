import { z } from "zod";
import {
  candidateSchema, chatReplySchema, collectResultSchema, documentSchema, eventDetailSchema, eventPageSchema, eventSchema,
  eventTypeSchema, newSignalSchema, processResultSchema, sourceCheckSchema, sourceRunSchema, stateSchema,
  type Candidate, type ChatMessage, type ChatReply, type CollectResult, type CollectedDocument, type DocumentStage,
  type EventFilters, type EventPage, type EventType, type NewSignal, type ProcessResult, type SourceCheck, type SourceEvent,
  type SourceEventDetail, type SourceRun, type WorkspaceState,
} from "../domain/models";

export type CandidatePatch = Partial<Pick<Candidate, "status" | "owner" | "nextAction">>;
export interface InvestmentRepository {
  load(): Promise<WorkspaceState>;
  create(input: NewSignal): Promise<Candidate>;
  update(id: string, patch: CandidatePatch): Promise<Candidate>;
  exportApproved(): Promise<{ csv: string; count: number; exportId: string }>;
  /** News feeds are collected at once; event sources answer queued: true and the worker collects them. */
  collectSource(id: string): Promise<CollectResult>;
  checkSource(id: string): Promise<SourceCheck>;
  sourceRuns(id: string): Promise<SourceRun[]>;
  events(filters?: EventFilters): Promise<EventPage>;
  event(id: number): Promise<SourceEventDetail>;
  candidateEvents(id: string): Promise<SourceEvent[]>;
  eventTypes(): Promise<EventType[]>;
  processPending(limit: number): Promise<ProcessResult>;
  documents(stage?: DocumentStage): Promise<CollectedDocument[]>;
  chat(messages: ChatMessage[]): Promise<ChatReply>;
  riskCheck(id: string, inn?: string, force?: boolean): Promise<Candidate>;
  subscribe(listener: () => void): () => void;
}

export class HttpInvestmentRepository implements InvestmentRepository {
  constructor(
    private readonly baseUrl = "/api",
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}
  private async request<T>(path: string, schema: z.ZodType<T>, method = "GET", body?: unknown, timeout = 15000): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), timeout);
    try {
      const response = await this.fetcher(`${this.baseUrl}${path}`, {
        method, signal: controller.signal, cache: "no-store",
        headers: body === undefined ? undefined : { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!response.ok) {
        const error = await response.json().catch(() => null);
        throw new Error(typeof error?.detail === "string" ? error.detail : "Сервер временно недоступен. Повторите попытку.");
      }
      const result = schema.safeParse(await response.json());
      if (!result.success) throw new Error("Сервер вернул некорректные данные. Повторите загрузку.");
      return result.data;
    } catch (error) {
      if (error instanceof TypeError || controller.signal.aborted) {
        throw new Error("Нет связи с сервером. Проверьте подключение и повторите попытку. Изменения могли не сохраниться.");
      }
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }
  load() { return this.request("/state", stateSchema); }
  async create(input: NewSignal) {
    return this.request("/candidates", candidateSchema, "POST", newSignalSchema.parse(input));
  }
  update(id: string, patch: CandidatePatch) {
    return this.request(`/candidates/${encodeURIComponent(id)}`, candidateSchema, "PATCH", patch);
  }
  exportApproved() {
    return this.request("/exports", z.object({ csv: z.string(), count: z.number().int().positive(), exportId: z.string() }), "POST");
  }
  collectSource(id: string) {
    return this.request(`/sources/${encodeURIComponent(id)}/collect`, collectResultSchema, "POST", undefined, 30000);
  }
  checkSource(id: string) {
    return this.request(`/sources/${encodeURIComponent(id)}/check`, sourceCheckSchema, "POST", undefined, 20000);
  }
  sourceRuns(id: string) {
    return this.request(`/sources/${encodeURIComponent(id)}/runs`, sourceRunSchema.array());
  }
  events(filters: EventFilters = {}) {
    return this.request(`/events${eventQuery(filters)}`, eventPageSchema);
  }
  event(id: number) {
    return this.request(`/events/${encodeURIComponent(String(id))}`, eventDetailSchema);
  }
  candidateEvents(id: string) {
    return this.request(`/candidates/${encodeURIComponent(id)}/events`, eventSchema.array());
  }
  eventTypes() {
    return this.request("/event-types", eventTypeSchema.array());
  }
  processPending(limit: number) {
    return this.request("/pipeline/process", processResultSchema, "POST", { limit }, 120000);
  }
  documents(stage?: DocumentStage) {
    return this.request(`/documents?limit=40${stage ? `&stage=${stage}` : ""}`, documentSchema.array());
  }
  chat(messages: ChatMessage[]) {
    return this.request("/chat", chatReplySchema, "POST", { messages }, 120000);
  }
  riskCheck(id: string, inn?: string, force = false) {
    return this.request(`/candidates/${encodeURIComponent(id)}/risk-check`, candidateSchema, "POST", { ...(inn ? { inn } : {}), force }, 90000);
  }
  subscribe(listener: () => void) {
    // Shared state refreshes without requiring a WebSocket or extra container.
    const refresh = () => { if (document.visibilityState === "visible") listener(); };
    const timer = window.setInterval(refresh, 15000);
    window.addEventListener("focus", refresh);
    document.addEventListener("visibilitychange", refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener("focus", refresh);
      document.removeEventListener("visibilitychange", refresh);
    };
  }
}

export function eventQuery(filters: EventFilters) {
  const params = new URLSearchParams();
  for (const type of filters.type ?? []) params.append("type", type);
  const single: Record<string, string | number | undefined> = {
    inn: filters.inn, company: filters.company, municipality: filters.municipality, source: filters.source,
    processing: filters.processing, candidate: filters.candidate, date_from: filters.dateFrom, date_to: filters.dateTo,
    limit: filters.limit, offset: filters.offset,
  };
  for (const [key, value] of Object.entries(single)) {
    if (value !== undefined && value !== "") params.set(key, String(value));
  }
  const query = params.toString();
  return query ? `?${query}` : "";
}

export const repository: InvestmentRepository = new HttpInvestmentRepository();
