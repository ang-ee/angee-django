---
"@angee/ui": minor
---

`ActionMenu` shows no trigger while none of its children renders a menu item,
so a record whose only contributed verb renders nothing has no empty `⋮`
popover. Every styled item of `DropdownMenu` and `ContextMenu` registers with
the enclosing menu through `useActionMenuItem`, and `ActionMenuContextValue`
gains a required `registerItem`; both come from
`@angee/ui/ui/action-menu-context`. Upgrade: an item rendered inside an
`ActionMenu` without these parts calls `useActionMenuItem()` to be counted, and
a hand-built `ActionMenuContext` value supplies `registerItem`.
