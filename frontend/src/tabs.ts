/** Tab controllers: the top-level views, and the architecture diagram switcher. */

export interface TabSet {
  /** Show a panel by id, e.g. from a `#technical` deep link. */
  activate(panelId: string): void;
  current(): string;
}

/**
 * Top-level tabs. Buttons carry `data-panel`; the panels are siblings with
 * matching ids, hidden rather than detached so scroll and form state survive a
 * switch.
 */
export function setupViewTabs(
  tablist: HTMLElement,
  panels: HTMLElement[],
  onChange?: (panelId: string) => void,
): TabSet {
  const tabs = [...tablist.querySelectorAll<HTMLButtonElement>("[data-panel]")];
  let active = tabs[0]?.dataset.panel ?? "";

  const activate = (panelId: string, emit = true): void => {
    if (!tabs.some((tab) => tab.dataset.panel === panelId)) return;
    active = panelId;
    for (const tab of tabs) {
      const selected = tab.dataset.panel === panelId;
      tab.classList.toggle("is-active", selected);
      tab.setAttribute("aria-selected", String(selected));
      tab.tabIndex = selected ? 0 : -1;
    }
    for (const panel of panels) {
      panel.hidden = panel.id !== panelId;
    }
    if (emit) onChange?.(panelId);
  };

  for (const tab of tabs) {
    tab.addEventListener("click", () => activate(tab.dataset.panel ?? ""));
  }

  // Left/right arrows move between tabs, which is what a tablist is expected to do.
  tablist.addEventListener("keydown", (event) => {
    const offset = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (offset === 0) return;
    event.preventDefault();
    const index = tabs.findIndex((tab) => tab.dataset.panel === active);
    const next = tabs[(index + offset + tabs.length) % tabs.length];
    if (next) {
      activate(next.dataset.panel ?? "");
      next.focus();
    }
  });

  // Silent: the first paint is not a user choice, and announcing it would let a
  // listener overwrite a deep link before it has been read.
  activate(active, false);
  return { activate: (panelId: string) => activate(panelId), current: () => active };
}

/** Architecture diagram tabs: anchors drive the frame, JS keeps the state in sync. */
export function setupArchitectureTabs(
  tabs: HTMLElement,
  frame: HTMLIFrameElement,
): void {
  const items = [...tabs.querySelectorAll<HTMLAnchorElement>("a[href]")];

  const activate = (target: HTMLAnchorElement): void => {
    for (const item of items) {
      const active = item === target;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
    }
    const href = target.getAttribute("href");
    if (href) frame.setAttribute("src", href);
  };

  for (const item of items) {
    item.addEventListener("click", (event) => {
      // The `target` attribute already works without JS; take over to keep the
      // active tab, the frame, and assistive technology in sync.
      event.preventDefault();
      activate(item);
    });
  }
}
