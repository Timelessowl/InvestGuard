import { afterEach, describe, expect, it, vi } from "vitest";

type FakeScript = { src?: string; dataset: Record<string, string>; onload?: () => void; onerror?: () => void };

function fakeDom() {
  const scripts: FakeScript[] = [];
  vi.stubGlobal("window", {});
  vi.stubGlobal("document", {
    createElement: () => ({ dataset: {} }),
    head: { append: (script: FakeScript) => scripts.push(script) },
  });
  return scripts;
}

const config = (websiteId: string | null) => async () => new Response(JSON.stringify({ websiteId }));

describe("analytics", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it("queues events until the tracker loads, then sends them in order", async () => {
    const scripts = fakeDom();
    const { initAnalytics, track } = await import("./analytics");
    track("card_open", { card: "INV-0001" });
    await initAnalytics(config("site-1"));
    expect(scripts).toHaveLength(1);
    expect(scripts[0].src).toBe("/stats/script.js");
    expect(scripts[0].dataset.websiteId).toBe("site-1");
    expect(scripts[0].dataset.fetchCredentials).toBe("same-origin");
    const sent: unknown[] = [];
    window.umami = { track: (...args) => sent.push(args) };
    scripts[0].onload?.();
    track("export", { count: 2 });
    expect(sent).toEqual([["card_open", { card: "INV-0001" }], ["export", { count: 2 }]]);
  });

  it("stays silent when analytics is off or the tracker fails to load", async () => {
    const scripts = fakeDom();
    const { initAnalytics, track } = await import("./analytics");
    await initAnalytics(config(null));
    track("card_open");
    expect(scripts).toHaveLength(0);
    vi.resetModules();
    const again = await import("./analytics");
    await again.initAnalytics(config("site-1"));
    scripts[0].onerror?.();
    const sent: unknown[] = [];
    window.umami = { track: (...args) => sent.push(args) };
    again.track("card_open");
    expect(sent).toEqual([]);
  });
});
