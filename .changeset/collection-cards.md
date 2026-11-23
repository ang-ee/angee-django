---
"@angee/ui": minor
"@angee/app": minor
---

Cards are one contract on the gallery and the board. A card renderer is
`renderCard(row, context)` (`CardRenderer`), where `CardRenderContext` is a
union on `kind`: a gallery card gets `selected`, a board card `selected`,
`dragging` and `laneId`. It is called as a plain function, so it holds no hooks.
`ListView` and `ResourceList` offer a built-in `gallery` view kind where the
list has a card to draw: the page's `renderCard`, its `gallery` card fields, or
the model's card, registered as the one child of `<app.Model>#card` (content
`{ render, frame? }`, the fields it reads in `requiredFields`), which draws that
model's board and gallery wherever a page declares no card; `createApp` refuses
a card on `resource#card` or one reading unknown fields. Cards share one frame
recipe (`cardFrameVariants`, `inset` or `flush` through
`ListViewProps.cardFrame`, and `CardSelection`), and board cards show and toggle
selection. `RowsListView`'s grid is the `gallery` view kind, in the URL as
`?view=gallery`. An addon container may declare `unique: "address"`, at most
one child per rendered address. Upgrade: `GalleryView` takes `getRowId` in place
of `rowKey`, and takes its title from `titleField` or the new `cardLabel`
instead of a `title` or `name` field; `ResourceViewSwitcher` no longer takes
`mode` (`"layout"` included) or a type parameter, and `ResourceToolbar` no
longer takes a `viewSwitcher` node (`chrome.viewSwitcher` stays), so a page
offers its kinds through the collection's views.
