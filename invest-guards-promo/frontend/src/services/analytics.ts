// Product analytics in self-hosted Umami; a no-op until the backend reports a website id.
import type { Candidate } from "../domain/models";

export type EventData = Record<string, string | number | boolean>;
type Tracker = { track: (name: string, data?: EventData) => unknown };
declare global {
  interface Window {
    umami?: Tracker;
  }
}

const MAX_PENDING = 100;
const pending: Array<[string, EventData | undefined]> = [];
let enabled: boolean | null = null;

function disable() {
  enabled = false;
  pending.length = 0;
}

export function track(name: string, data?: EventData) {
  if (enabled === false) return;
  if (window.umami) window.umami.track(name, data);
  else if (pending.length < MAX_PENDING) pending.push([name, data]);
}

export async function initAnalytics(fetcher: typeof fetch = (...args) => fetch(...args)) {
  try {
    const response = await fetcher("/api/tracking", { cache: "no-store" });
    const config = response.ok ? ((await response.json()) as { websiteId?: string | null }) : null;
    if (!config?.websiteId) return disable();
    enabled = true;
    const script = document.createElement("script");
    script.src = "/stats/script.js";
    script.defer = true;
    script.dataset.websiteId = config.websiteId;
    // Umami defaults to omitting credentials, but the same-origin proxy can require app login.
    script.dataset.fetchCredentials = "same-origin";
    script.onload = () => {
      for (const [name, data] of pending.splice(0)) window.umami?.track(name, data);
    };
    script.onerror = disable;
    document.head.append(script);
  } catch {
    disable();
  }
}

export function cardProps(candidate: Candidate): EventData {
  return {
    card: candidate.id,
    origin: candidate.origin,
    status: candidate.status,
    risk: candidate.risk?.verdict ?? "none",
    score: candidate.score,
    sector: candidate.sector,
  };
}

export function secondsSince(start: number) {
  return Math.round((Date.now() - start) / 1000);
}
