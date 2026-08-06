/** The architecture tabs must keep the frame and the selected state in sync. */
import { beforeEach, expect, test } from "bun:test";
import { Window } from "happy-dom";

import { setupArchitectureTabs } from "./diagram.ts";

let window: Window;

beforeEach(() => {
  window = new Window({ url: "http://localhost:3000" });
  globalThis.document = window.document as unknown as Document;
});

function mount(): { tabs: HTMLElement; frame: HTMLIFrameElement } {
  const tabs = document.createElement("div");
  tabs.innerHTML = `
    <a class="arch-tab is-active" role="tab" aria-selected="true"
       href="/api/architecture-diagrams/architecture-1-azure-language">One</a>
    <a class="arch-tab" role="tab" aria-selected="false"
       href="/api/architecture-diagrams/architecture-2-mai-realtime-deepseek">Two</a>
    <a class="arch-tab" role="tab" aria-selected="false"
       href="/api/architecture-diagrams/architecture-3-mai-batch-deepseek">Three</a>`;
  const frame = document.createElement("iframe");
  frame.setAttribute("src", "/api/architecture-diagrams/architecture-1-azure-language");
  document.body.append(tabs, frame);
  setupArchitectureTabs(tabs, frame);
  return { tabs, frame };
}

test("selecting a tab swaps the diagram and moves the selected state", () => {
  const { tabs, frame } = mount();
  const [first, second] = [...tabs.querySelectorAll<HTMLAnchorElement>("a")];

  second!.click();

  expect(frame.getAttribute("src")).toBe(
    "/api/architecture-diagrams/architecture-2-mai-realtime-deepseek",
  );
  expect(second!.getAttribute("aria-selected")).toBe("true");
  expect(second!.classList.contains("is-active")).toBe(true);
  expect(first!.getAttribute("aria-selected")).toBe("false");
  expect(first!.classList.contains("is-active")).toBe(false);
});

test("tab clicks do not navigate the page away from the dashboard", () => {
  const { tabs } = mount();
  const third = [...tabs.querySelectorAll<HTMLAnchorElement>("a")][2]!;

  const event = new (window as unknown as { Event: typeof Event }).Event("click", {
    bubbles: true,
    cancelable: true,
  });
  third.dispatchEvent(event);

  expect(event.defaultPrevented).toBe(true);
});
