import { beforeAll, expect, test } from "bun:test";
import { Window } from "happy-dom";

import {
  renderCalculator,
  syncMeasuredCallLength,
} from "./calculator.ts";

let DomEvent: typeof Event;

beforeAll(() => {
  const window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
  globalThis.localStorage = window.localStorage;
  DomEvent = window.Event as unknown as typeof Event;
});

test("source changes synchronize measured call length until the seller overrides it", () => {
  const host = document.createElement("div");
  syncMeasuredCallLength(host, 300);
  renderCalculator(host);

  const input = host.querySelector<HTMLInputElement>("#volume-call-minutes")!;
  expect(input.value).toBe("5");

  syncMeasuredCallLength(host, 120);
  expect(input.value).toBe("2");

  input.value = "7";
  input.dispatchEvent(new DomEvent("input", { bubbles: true }));
  syncMeasuredCallLength(host, 60);
  expect(input.value).toBe("7");
});
