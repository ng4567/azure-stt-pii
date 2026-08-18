import { escapeHtml } from "./format.ts";
import { PRICING_DOCUMENTATION } from "./pricing.ts";

/** Shared source block shown beside every detailed cost presentation. */
export function renderPricingSources(): HTMLElement {
  const aside = document.createElement("aside");
  aside.className = "pricing-sources";
  aside.innerHTML = `
    <div>
      <strong>Pricing sources</strong>
      <p>
        Rates are public Azure list prices for East US. The linked Microsoft pages
        and the customer's agreement remain the source of truth.
      </p>
    </div>
    <ul>
      ${PRICING_DOCUMENTATION.map(
        (source) => `
          <li>
            <a href="${escapeHtml(source.url)}" target="_blank" rel="noreferrer">
              ${escapeHtml(source.label)}
            </a>
            <span>${escapeHtml(source.description)}</span>
          </li>`,
      ).join("")}
    </ul>`;
  return aside;
}
