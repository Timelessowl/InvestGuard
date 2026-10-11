import { z } from "zod";
import { statusSchema } from "../domain/models";
import type { InvestmentRepository } from "./repository";

interface ModelContext {
  registerTool: (tool: {
    name: string;
    title: string;
    description: string;
    inputSchema: object;
    annotations: object;
    execute: (input: unknown) => Promise<unknown>;
  }) => unknown;
  unregisterTool?: (name: string) => unknown;
}
// Preserve the promo's optional browser integration without making it a runtime dependency.
export function registerWebMcp(
  api: InvestmentRepository,
  mutate: <T>(operation: () => Promise<T>) => Promise<T>,
) {
  const context = (document as Document & { modelContext?: ModelContext })
    .modelContext;
  if (!context?.registerTool) return;
  const names: string[] = [];
  const register = (tool: Parameters<ModelContext["registerTool"]>[0]) => {
    try {
      Promise.resolve(context.registerTool(tool)).catch(() => {});
      names.push(tool.name);
    } catch {
      /* Experimental API may not be enabled. */
    }
  };
  register({
    name: "list_investment_candidates",
    title: "Показать инвестиционные проекты",
    description:
      "Текущие демонстрационные инвестиционные карточки с оценкой и статусом.",
    inputSchema: {
      type: "object",
      properties: {
        status: { type: "string", enum: ["all", ...statusSchema.options] },
      },
      additionalProperties: false,
    },
    annotations: { readOnlyHint: true },
    async execute(input) {
      const filter = z
        .object({
          status: z.enum(["all", ...statusSchema.options]).default("all"),
        })
        .strict()
        .parse(input ?? {});
      const { candidates } = await api.load();
      return candidates
        .filter(
          (candidate) =>
            filter.status === "all" || candidate.status === filter.status,
        )
        .map(({ id, company, signal, score, status }) => ({
          id,
          company,
          signal,
          score,
          status,
        }));
    },
  });
  register({
    name: "set_candidate_review_status",
    title: "Изменить решение по проекту",
    description:
      "Сохраняет экспертный статус одной карточки на сервере и обновляет очередь.",
    inputSchema: {
      type: "object",
      properties: {
        id: { type: "string" },
        status: { type: "string", enum: ["review", "approved", "rejected"] },
      },
      required: ["id", "status"],
      additionalProperties: false,
    },
    annotations: { readOnlyHint: false },
    async execute(input) {
      const data = z
        .object({
          id: z.string().min(1),
          status: z.enum(["review", "approved", "rejected"]),
        })
        .strict()
        .parse(input);
      const candidate = await mutate(() =>
        api.update(data.id, { status: data.status }),
      );
      return { id: candidate.id, status: candidate.status, updated: true };
    },
  });
  return () => {
    for (const name of names) {
      try {
        context.unregisterTool?.(name);
      } catch {
        /* Optional API cleanup. */
      }
    }
  };
}
