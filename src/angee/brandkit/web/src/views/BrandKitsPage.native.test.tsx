// @vitest-environment happy-dom
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { expect, test } from "vitest";
import type { GraphQLWireRequest } from "@angee/storybook/testing";

import { BrandKitStory } from "./BrandKitsPage.stories";

/** The kits' row links, in the order the list shows them. */
function listedKits(): string[] {
  return screen.queryAllByRole("link", { name: /^Open (Angee|Sandstone)$/ }).map((link) => link.getAttribute("aria-label") ?? "");
}

/** The kit form's section headings and field labels, in the order the sheet shows them. */
async function sheetOutline(): Promise<string[]> {
  const form = (await screen.findByRole("heading", { name: "Kit" })).closest("form");
  if (!form) throw new Error("The kit form renders no form element.");
  return [...form.querySelectorAll("h2, h3, label")].map((node) => node.textContent?.trim() ?? "").filter(Boolean);
}

/** The form below its title: every section in its declared place, both long texts in theirs. */
const KIT_OUTLINE = [
  "Kit", "Default kit",
  "Identity palette", "Brand color", "Accent color", "Neutral tint",
  "Surfaces and navigation", "Page background", "Cards and panels", "App rail",
  "Status palette", "Success", "Warning", "Danger", "Information",
  "Readability in mail", "Warnings",
  "Fonts", "Headings", "Body text",
  "Mail footer", "Organization", "Postal address",
  "Voice", "Tone and style",
];

test.each([
  ["a saved kit", "sandstone", KIT_OUTLINE],
  ["the create form", "new", KIT_OUTLINE.filter((label) => label !== "Readability in mail" && label !== "Warnings")],
] as const)("%s shows nothing but its title above the Kit section, the address in the mail footer and the voice last", async (_case, open, outline) => {
  render(<BrandKitStory open={open} />);
  await screen.findByLabelText("Tone and style");
  expect(await sheetOutline()).toEqual(outline);
  const footer = screen.getByRole("heading", { name: "Mail footer" }).closest("section");
  expect(footer?.contains(screen.getByLabelText("Postal address"))).toBe(true);
});

test("the default kit lists first, and a kit saved as the default lists first once the list reads again", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<BrandKitStory onRequest={(request) => requests.push(request)} />);
  await waitFor(() => expect(listedKits()).toEqual(["Open Angee", "Open Sandstone"]));
  fireEvent.click(screen.getByRole("link", { name: "Open Sandstone" }));
  await screen.findByDisplayValue("system-ui, sans-serif");
  const isDefault = screen.getByRole("switch", { name: "Default kit" });
  expect(isDefault.getAttribute("aria-checked")).toBe("false");
  fireEvent.click(isDefault);
  await waitFor(() => expect(isDefault.getAttribute("aria-checked")).toBe("true"));
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("Changes saved");
  expect(requests.find(({ fields }) => fields.includes("update_brand_kits_by_pk"))?.variables._set).toMatchObject({ is_default: true });
  const listReads = requests.filter(({ fields }) => fields.includes("brand_kits")).length;
  fireEvent.click(within(screen.getByRole("navigation", { name: "Primary navigation" })).getByRole("link", { name: "Brand kits" }));
  await waitFor(() => expect(listedKits()).toEqual(["Open Sandstone", "Open Angee"]));
  expect(requests.filter(({ fields }) => fields.includes("brand_kits")).length).toBeGreaterThan(listReads);
});

test("a saved kit's form shows one warning per colour mail moves far, and no logo input", async () => {
  render(<BrandKitStory open="sandstone" />);
  await waitFor(() => expect(document.body.textContent).toContain(
    "Neutral tint: muted text in light mail moves far from this colour to stay readable.",
  ));
  expect(document.body.textContent).toContain("Brand color: a link in light mail moves far from this colour to stay readable.");
  expect(screen.queryByLabelText(/logo/i)).toBeNull();
});

test("a kit whose colours mail wears as stated says so in place of warnings", async () => {
  render(<BrandKitStory open="angee" />);
  await waitFor(() => expect(document.body.textContent).toContain("Light and dark mail show these colours as stated."));
});

test("the create form starts empty and has no contrast group until the kit is saved", async () => {
  render(<BrandKitStory open="new" />);
  const body = await screen.findByLabelText("Body text");
  expect((body as HTMLInputElement).value).toBe("");
  expect(screen.queryByText("Readability in mail")).toBeNull();
});

test("a body font stack the console refuses shows its message under the field", async () => {
  const requests: GraphQLWireRequest[] = [];
  render(<BrandKitStory open="angee" refuseFontStack onRequest={(request) => requests.push(request)} />);
  const body = await screen.findByLabelText("Body text") as HTMLInputElement;
  await waitFor(() => expect(body.value).toBe("Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"));
  fireEvent.change(body, { target: { value: "Inter" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(body.getAttribute("aria-invalid")).toBe("true"));
  const described = (body.getAttribute("aria-describedby") ?? "").split(" ").map((id) => document.getElementById(id)?.textContent ?? "");
  expect(described.some((text) => text.includes("End the font stack with a generic family, such as sans-serif."))).toBe(true);
  expect(requests.find(({ fields }) => fields.includes("update_brand_kits_by_pk"))?.variables._set).toMatchObject({ font_body: "Inter" });
  expect(screen.queryByText("Changes saved")).toBeNull();
});
