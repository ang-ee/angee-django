// @vitest-environment happy-dom
// @vitest-environment-options {"settings":{"navigation":{"disableMainFrameNavigation":true,"disableChildPageNavigation":true}}}
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, test, vi } from "vitest";

import { InAppLinkProvider } from "../lib/in-app-link";
import { ContextMenu } from "./context-menu";
import { DropdownMenu } from "./dropdown-menu";

afterEach(cleanup);

test.each([DropdownMenu, ContextMenu])("shared menu links follow the owner and retain native modified activation", async (Menu) => {
  const navigate = vi.fn();
  const onClick = vi.fn();
  render(<InAppLinkProvider navigate={navigate}>
    <Menu.Root open><Menu.Portal><Menu.Positioner><Menu.Content>
      <Menu.LinkItem href="/records/7?view=all" onClick={onClick}>Record</Menu.LinkItem>
    </Menu.Content></Menu.Positioner></Menu.Portal></Menu.Root>
  </InAppLinkProvider>);
  const link = await screen.findByRole("menuitem", { name: "Record" });
  fireEvent.click(link, { metaKey: true });
  expect(navigate).not.toHaveBeenCalled();
  expect(fireEvent.click(link)).toBe(false);
  expect(onClick).toHaveBeenCalledTimes(2);
  expect(navigate).toHaveBeenCalledExactlyOnceWith("/records/7?view=all");
});
