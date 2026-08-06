/** Architecture tabs: anchors drive the diagram frame, JS tracks the active one. */
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
