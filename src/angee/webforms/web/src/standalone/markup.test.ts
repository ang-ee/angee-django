// @vitest-environment happy-dom
import { expect, test } from "vitest";

import { linksInNewTab } from "./markup";

test("every link of a form's markup opens in a new tab, without a reference back to the form", () => {
  const html = linksInNewTab(
    '<p>Read the <a href="https://example.org/rules">rules</a> or <a href="mailto:hello@example.org">write to us</a>.</p>'
    + "<ul><li><em>One</em></li></ul>",
  );
  const parsed = new DOMParser().parseFromString(html, "text/html");
  expect([...parsed.querySelectorAll("a")].map((link) => [link.getAttribute("href"), link.getAttribute("target"), link.getAttribute("rel")]))
    .toEqual([
      ["https://example.org/rules", "_blank", "noopener noreferrer"],
      ["mailto:hello@example.org", "_blank", "noopener noreferrer"],
    ]);
  // The rest of the markup is kept as written.
  expect(parsed.querySelector("p")?.textContent).toBe("Read the rules or write to us.");
  expect(parsed.querySelector("ul > li > em")?.textContent).toBe("One");
});

test("markup without a link is kept as written", () => {
  expect(linksInNewTab("<p>Thank you.</p>")).toBe("<p>Thank you.</p>");
});
