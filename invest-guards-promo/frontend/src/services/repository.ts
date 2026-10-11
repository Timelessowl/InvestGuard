import { z } from "zod";
import {
  candidateSchema, newSignalSchema, sourceSchema, stateSchema,
  type Candidate, type DataSource, type NewSignal, type WorkspaceState,
} from "../domain/models";

export type CandidatePatch = Partial<Pick<Candidate, "status" | "owner" | "nextAction">>;
export interface InvestmentRepository {
  load(): Promise<WorkspaceState>;
  create(input: NewSignal): Promise<Candidate>;
  update(id: string, patch: CandidatePatch): Promise<Candidate>;
  exportApproved(): Promise<{ csv: string; count: number; exportId: string }>;
  retrySource(id: string): Promise<DataSource>;
  subscribe(listener: () => void): () => void;
}

export class HttpInvestmentRepository implements InvestmentRepository {
  constructor(
    private readonly baseUrl = "/api",
    private readonly fetcher: typeof fetch = (...args) => fetch(...args),
  ) {}
  private async request<T>(path: string, schema: z.ZodType<T>, method = "GET", body?: unknown): Promise<T> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
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
  retrySource(id: string) {
    return this.request(`/sources/${encodeURIComponent(id)}/retry`, sourceSchema, "POST");
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

export const repository: InvestmentRepository = new HttpInvestmentRepository();
