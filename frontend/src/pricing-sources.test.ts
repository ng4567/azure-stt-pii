import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import { PRICING_DOCUMENTATION } from "./pricing.ts";
import { renderPricingSources } from "./pricing-sources.ts";

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
});

test("pricing sources link every configured rate to Microsoft documentation", () => {
  const sources = renderPricingSources();
  const links = [...sources.querySelectorAll<HTMLAnchorElement>("a")];

  expect(links.map((link) => link.href)).toEqual(
    PRICING_DOCUMENTATION.map((source) => source.url),
  );
  expect(links.every((link) => link.target === "_blank")).toBe(true);
  expect(sources.textContent).toContain("East US");
  expect(sources.textContent).toContain("source of truth");
});
