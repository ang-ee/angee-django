# @angee/ui

## 0.2.0

### Minor Changes

- 37387b7: The signed-in user carries `hasUsablePassword` (`AuthUser` in `@angee/app`,
  `RuntimeAuthUser` in `@angee/ui`), so a user-menu item can hide a password
  action for a session without one. The identity query reads it from the user
  type's new `has_usable_password` field, so upgrade the Angee backend with the
  packages. A dialog opened from a contributed user-menu item stays mounted and
  returns focus to the menu's trigger when it closes. A form field may declare
  `autoComplete`, which the password and textarea widgets pass to their input.
  `useActionMutation`, `useActionResultMutation` and
  `useRecordChromeActionMutation` accept `errorNotification: false` and
  `transient`, for a verb carrying a secret input: its mutation state is cleared
  once it settles, and a second call while one runs rejects.
- ae89168: An `ActionFormDialog` that checks its own rules (its arguments are declared as
  `args`, or its definition brings no resolver) shows a failing value's message as
  the value changes. Before, it checked nothing before the first submit while its
  submit button was disabled as soon as a rule failed, so a value too short or a
  required argument typed and then cleared disabled the button with no message on
  screen, and a person could not submit to see one. Now the message shows under
  its field at the change, while the button is disabled, and goes when the value
  is corrected, as in a `MutationDialog`. A value that already breaks a rule when
  the dialog opens, a required field left empty or a prefilled text shorter than
  its minimum, still disables the button with no message until the person edits
  that field. Editing one member of an object field checks the whole field, so it
  also shows the messages of the field's other members that break a rule, though
  the person has not touched them, while a failing field outside it that the
  person has not touched stays silent. A dialog whose definition brings a
  resolver, such as the one `jsonSchemaActionArgs` returns, still checks from the
  first submit. `useActionForm` takes React Hook Form's `mode` as the new
  `UseActionFormOptions.mode`; a caller that passes none still checks from the
  first submit.
- 37387b7: `ActionMenu` shows no trigger while none of its children renders a menu item,
  so a record whose only contributed verb renders nothing has no empty `⋮`
  popover. Every styled item of `DropdownMenu` and `ContextMenu` registers with
  the enclosing menu through `useActionMenuItem`, and `ActionMenuContextValue`
  gains a required `registerItem`; both come from
  `@angee/ui/ui/action-menu-context`. Upgrade: an item rendered inside an
  `ActionMenu` without these parts calls `useActionMenuItem()` to be counted, and
  a hand-built `ActionMenuContext` value supplies `registerItem`.
- 37387b7: Over several apps, the app chooser and the expanded rail's caption show a
  declared brand's mark and name (the deployment's, the selected app's or its
  menu root's), and keep the framework mark and "Apps" otherwise; the first rail
  root's label and icon, which stand in for a missing brand elsewhere, are not
  shown there. An unregistered mark falls back to the framework glyph.
  `useDeclaredBrand()` returns the declared brand, or null, and
  `AppRuntime.brandFallback` (set by `createApp`, and on `appSelection`'s result)
  is `true` when the runtime brand is such a stand-in.
- 37387b7: An app may lock its theme: an app root's menu entry or an `ANGEE_UI.shell.apps`
  entry sets `themeLocked: true` beside its `theme` (`createApp` refuses a lock
  without one; `ANGEE_UI.shell` itself takes none). Within that app the locked
  theme and the host's options replace the person's, while their colour scheme
  still applies; their saved theme is kept for other apps, and the first-paint
  cache keeps their own theme, options and scheme. `AppearanceProvider` takes a
  `themeLocked` prop and `AppearanceState` exposes `themeLocked`.
  `RadioGroupItem` now puts `aria-describedby` on the focusable radio instead of
  its label.
- 37387b7: `AppearanceState` has `drafting`, whether an appearance draft is open, so a
  settings page can keep its draft open by it instead of by the identity of
  `openDraft`. `AppearanceProvider` resolves the appearance once per input (the
  saved preferences, the theme catalogue, the host defaults, the system scheme,
  the theme lock and the draft), so a render that changes none of them no longer
  re-applies the token layers to the document or re-reads and re-encodes the
  first-paint cache.
- ab02c11: `buildColumns` takes the list's resolved group axes instead of resolving them again:
  `BuildColumnsOptions.groupStack` is replaced by `axes`, the `GroupAxis` list the table
  state already resolves once for the table's grouping and its headers. Nothing a list
  renders or reads changes. **Upgrade:** a caller of `buildColumns` from
  `@angee/ui/views/resource-view-list-body` passes
  `axes: tableGroupAxes(groupStack, metadata, columns, query)` from the same entry in place
  of `groupStack`.
- 37387b7: `CalendarView` gains a `list` mode, a month's occurrences listed by day, beside
  `month`, `week` and `day`; `CALENDAR_VIEW_MODES` and `CalendarViewMode` include
  it, so the URL (`?mode=list`) and the toolbar's mode switch offer it. It steps
  and is titled by month. `@angee/ui` now depends on `@fullcalendar/list`.
  Upgrade: code that maps every `CalendarViewMode` exhaustively handles `list`.
- 37387b7: A calendar range selection reports whether whole days were selected:
  `CalendarView`'s `onSelectRange`, `CalendarViewSpec.onSelectRange` and
  `ResourceListCalendarSpec.createDefaults` receive FullCalendar's all-day flag
  as a third argument, `(start, end, allDay)`; when it is true, `end` is the
  exclusive midnight after the last day. A quick-create can seed an all-day event
  from a month-view day click instead of a midnight-to-midnight timed one.
- 37387b7: A calendar filters by source. An occurrence source may declare a `key` (letters,
  digits, `_`, `-` and `.`, unique on its page) and a `label` (content or an addon
  message key, which needs a `key`; the calendar throws on a broken rule when it
  renders). When the calendar has more than one source, each labelled one gets a
  toolbar toggle (`ResourceToolbarViewControls.sources`), and a hidden source
  stops fetching and leaves the grid. The hidden keys are resource-view state,
  `ResourceViewState.hiddenSources`, set through the resource view's
  `setHiddenSources`, carried in the URL as `?hiddenSources=` and kept by saved
  and shipped views, so links and presets reproduce the calendar. When every
  source is hidden, a note says so and offers Show all.
- 37387b7: A picked date-time is stored as the instant the viewer meant:
  `formatDateTimeStorage` and `DATETIME_STORAGE_FORMAT` (now
  `yyyy-MM-dd'T'HH:mm:ssxxx`, was `yyyy-MM-dd'T'HH:mm`) write the local wall time
  with seconds and its UTC offset, so every `datetime` widget (record forms,
  filters, form specs, action dialogs) sends an instant instead of a naive time
  the server reads in its own zone. `calendarDay(date, timeZone)`, the
  `yyyy-MM-dd` day an instant falls on in a zone, and `isStorageDate` are new, and
  `dateFromUnknown` is now exported from `@angee/ui` as well as
  `@angee/ui/widgets/date-format`. A date formatter given a time zone the browser
  does not know reads in the viewer's zone and warns once instead of throwing.
  `AbsoluteTime` and list date cells read a bare `yyyy-MM-dd` value as that date
  with no time or zone, a filter chip shows a date, date-time or time operand as a
  human value, and `CalendarView` refuses a drop that would turn an all-day
  occurrence timed or the reverse. Upgrade: code that parses a widget's date-time
  with `DATETIME_STORAGE_FORMAT`, or compares it with a `yyyy-MM-ddTHH:mm` string,
  accepts the seconds-and-offset form (`2026-11-20T10:00:00+09:00`).
- 37387b7: `useCalendarWindow(sources, window, { hidden, enabled })` reads all of a page's
  occurrence sources in one batch read, so a source set that grows or shrinks
  never remounts the calendar grid, and sources sharing a document and variables
  share one request; a source whose key is `hidden`, or that sets
  `enabled: false`, leaves the batch. `@angee/metadata` exports
  `canonicalResourceModelLabels(metadata, labels)`, the pure form of
  `useCanonicalResourceModelLabels`. Upgrade: pass an array of sources where
  `useCalendarWindow` took one (`[calendarWindowSource(source)]` for a typed
  source), keep them on one data provider (mixed `dataProviderName`s now throw),
  and drop the removed `CalendarWindowFetch` type; `CalendarWindowOptions` names
  the new options.
- 37387b7: Cards are one contract on the gallery and the board. A card renderer is
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
- 37387b7: `CommandBand` takes no row while none of its items renders (every group holds
  only hidden or empty nodes), so a record whose contributed verbs offer nothing
  shows no empty strip above its form. The new `editable` prop keeps the row of an
  editable record's band in the layout, blank and hidden from assistive
  technology, so the Save and Discard its first edit brings shift nothing;
  `FormView` sets it from load on. A band placed inline, in a card or a dialog,
  collapses either way.
- 8d666f2: A value that breaks one of a field's rules now says which, in a record form and
  in a dialog without a schema resolver: too short, too long, out of range, too few
  or too many items, or the wrong type. Each said "This field is required." before.
  A cleared number field that is `omittable`, presence-required or bounded, and not
  nullable, now says "Enter a valid whole number." ("Enter a valid number." where
  its type is `number`) where it said the field was required; a required number
  field with no presence flag or bound still says the field is required. A missing
  value still says the field is required, and so does an empty text under a minimum
  length of one character in a field that is neither `omittable` nor nullable; in a
  field that is either, it says "Enter at least 1 character.". A dialog with a
  schema resolver says "Enter at least 1 character." in each case, and for a text
  declared nullable with `anyOf` also "Enter a valid empty value. Enter a valid
  value.". `@angee/ui/views/validation-errors` exports `jsonSchemaErrorMessage`,
  `JsonSchemaRule` and `fieldErrorsFromRules`.
  Upgrade: `structuredFieldErrorPaths` is renamed `structuredFieldErrors` and
  returns, for each failing path, `{ path, keyword, params }` (`FieldRuleError`)
  instead of the bare path; `FormViewSurface.requiredMessage`, which nothing read,
  is removed; code that read a form error's `type` as `required` for a length,
  range, item-count or type failure reads the rule's keyword instead (`minLength`,
  `maximum`, `maxItems`, `type`, …); and a host that embeds `@angee/ui` with its
  own i18n instance and its own `ui` translations (an app built with `createApp`
  cannot override the `ui` namespace) translates these messages in a singular and
  a plural form, which interpolate `{count}` where the old key interpolated
  `{limit}`: `form.validation.minLength` becomes `form.validation.minLength_one`
  and `form.validation.minLength_other`, `form.validation.maxLength` becomes
  `form.validation.maxLength_one` and `form.validation.maxLength_other`,
  `form.validation.minItems` becomes `form.validation.minItems_one` and
  `form.validation.minItems_other`, and `form.validation.maxItems` becomes
  `form.validation.maxItems_one` and `form.validation.maxItems_other`.
- 37387b7: `createApp` takes an optional `history`, the TanStack Router history the app
  navigates with (such as `createMemoryHistory()`), so a workshop or an embedded
  host can keep the app's navigation off the browser's address bar.
  `REFINE_CREATE_ID`, the record id a create route uses, is now also exported
  from `@angee/ui/runtime`.
- 69b3f6a: A record form offers a create-only implementation selector once more while the
  record its draft was made against stores a key that is not registered
  (`repairChoosable`, passed to `withModeLockedFields` as `choosable`). The lock
  reads that record, held while the form holds edits and moved by an accepted
  save, so neither a pick nor a re-read or another save meanwhile re-locks it. Choosing an implementation there
  sets what the server says it sets on that record (the resource's repair read,
  named by its metadata's `roots.repairChoices`: the config it carries over), so the save sends the selector with the config the
  person sees. The read is `<res>_repair_choices(id, field)` on the resource's own
  schema, answering under that root; each choice in it has the insert's choice
  shape (`key`, `category`, `defaults`, `config_schema`), its `defaults` the
  config carried over; a save that would send that config without its selector is refused
  on the client. Its mark reads "Its implementation cannot run until another is
  chosen. Once saved, the choice cannot be changed." Another key may be chosen
  before saving, never while a save is pending. After the save the selector is
  locked again. The repair asks that read through `@angee/refine`'s operation
  documents (the `repairChoices` kind the host's codegen generates per
  resource), so a resource without the root, or a host that
  generated no document for it, offers no repair; the insert's `BaseImplChoices`
  read is unchanged. A person the create flow
  does not admit, or the update refuses, sees the selector read-only with the
  server's words under the field. While the answer is pending the selector is
  `loading`; a refused answer keeps it read-only with the server's words under it,
  said once, beside the field's own description, and once, as the field's error,
  where a refused save says the same sentence (the control's description reads it
  once, and no hidden copy is left for a screen reader's browse mode, except the one
  a read-only field's mark is described by). The server's refusal of a save
  stays under the field across the re-read it causes, until the person changes
  that field or discards. A field bound through `BoundDescriptorField` takes the
  same states and lock. New public API: `WidgetControlProps.loading`,
  `LabeledDescriptorField`'s `loading`, and on `FormViewSaveSurface` `editBasis`
  (the record the draft was made against: a save sends its revision and the lock
  reads it), `recordField` and `repairState`. A save that would send the repair's
  config without its selector (its condition or its group hides it now) is
  refused on the client in words naming the selector.
  A page's own `createOnly` never gets the repair, and a page cannot unlock what
  the model's metadata locks.
- 37387b7: `DateTile` (with `dateTileVariants`, in sizes `sm`, `md` and `lg`) shows a date as
  its short month over a large day in the display face, in an optional `timeZone`.
  A missing or unreadable date shows a pending state (the new `dateTile.pending`
  message, or `pendingLabel`); otherwise the full date is the tooltip and what a
  screen reader hears. `@angee/ui/widgets/date-format` adds `formatDateParts`, the
  short month and the day of a date, each localized on its own.
- 8e4f4e1: A field descriptor carries the declared format of a value held as text:
  `FieldDescriptor.format`, one of `"date"`, `"date-time"` or `"time"`, the JSON
  Schema `format` of a string. It is the value's type, never its presentation,
  which stays the widget's. A form spec's parser sets it from a schema whose
  `string` declares one of those three formats, the backend's form spec among
  them (it now keeps a date member's `format` beside the widget that presents
  it, the date widget it selects or its author's own, an authored `json` widget
  aside; nothing asserts it there, since no validator is handed that spec);
  any other format, and a format on another type, is not carried: where a schema
  validator runs (a schema dialog, the server) it still reads it from the schema.
  A hand-written descriptor may declare it, and an action argument carries it.
  Declare it only on a value held as text: on a list or a switch it would make
  their empty value `null` too.

  What a field holds when it holds nothing reads it first: a descriptor with a
  declared `format` holds `null`, whatever its kind and widget, since `""` is no
  date, date and time or time; an explicit `string` kind without one still holds
  `""`, whatever its widget. Where a `string` with a declared format changed:

  | Field                                                                                      | Where                                                            | Before                                                          | Now                                                                                         |
  | ------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------- |
  | a nullable one holding `null`                                                              | the presence control, on a record form and in every dialog       | "Set value", which wrote `""`                                   | its own control with "Left empty", and no "Set value"                                       |
  | one the record lacks, neither omittable nor presence-required                              | record form: an edit that changed another member of its object   | `""`                                                            | `null`                                                                                      |
  | a nullable one with no default                                                             | record form: a create draft                                      | `""`                                                            | `null`                                                                                      |
  | one neither required nor nullable                                                          | record form: a create draft; a schema dialog; a `MutationDialog` | `""`                                                            | left out                                                                                    |
  | an item "Add item" adds to a list of them                                                  | a `MutationDialog`                                               | `""`: submit enabled                                            | `null`: submit disabled, "This field is required." under the item                           |
  |                                                                                            | a schema dialog                                                  | `""`: submit enabled, "Enter a valid date." on a submit attempt | `null`: submit disabled, with no message before a submit attempt, as for a list of integers |
  | an optional action argument declaring one under the `date` or the `text` widget, untouched | its `submit`                                                     | `""`                                                            | `null`                                                                                      |

  Of these rows, a member the backend's form spec emits reaches the first and the
  two "Add item" rows only: it marks every member omittable or presence-required
  and gives a nullable one a `null` default, so a nullable one already seeded
  `null`, and one the record lacks is neither seeded nor sent. The other rows are
  a hand-written or raw schema's. An
  optional action argument under the `datetime` widget already reached `submit`
  as `null`.

  No declared form of the framework or of its products has such a member. A
  `string` with only a date or time widget and no declared format keeps `""`. A
  date drawn as a text input (a raw schema's with no authored widget, or one
  under an authored `text` widget) seeds `null` where it is seeded at all, and
  clearing it writes `""`, which a schema dialog's validator refuses as before.

  Upgrade: a hand-written descriptor that carries a date, a date and time or a
  time as text declares its `format` (`kind: "string", format: "date"`), or it
  holds `""` when it holds nothing.

- 37387b7: The delete preview's title, blocked count and selection count use plural forms,
  so a one-record preview reads "Delete 1 record?". Their messages are now
  `deletePreview.title_one` / `_other`, `deletePreview.blockedCount_one` / `_other`
  and `deletePreview.recordCount_one` / `_other`. Upgrade: a host or addon that
  overrides `deletePreview.title`, `deletePreview.blockedCount` or
  `deletePreview.recordCount` renames its translation to the `_one` and `_other`
  keys.
- 37387b7: `DescriptorFieldList`, the field list `MutationDialog` and `ActionFormDialog`
  render, follows the record form's rules. With the new `unregisterHidden`, which
  both dialogs set, a field hidden by `showWhen` is not submitted and takes its
  initial value back when it shows again. A dialog honours a field's
  `prefillPreserveDirty` and `prefillReplace` as a record form does. A field's
  `resolve` now keeps its declared `name` in a record form too, as it already did
  in a dialog. `DescriptorFieldList` must be rendered inside an
  `ActionFormProvider`. Upgrade: pass `fields` where you passed `resolvedFields`,
  which is removed, and wrap a hand-built `DescriptorFieldList` in
  `<ActionFormProvider {...form}>`, with the `form` that `useActionForm` returns.
- 30afed5: An action or mutation dialog that the server refuses says each reason once:
  under the field it names when the dialog shows it, on its banner otherwise (a
  name only a `content` editor holds shows under the editor and on the banner).
  The server joins every reason into the outcome's `message` (a refused target's
  message is the verb's summary), and `actionOutcomeSubmitResult` (with
  `actionFormSubmitResult`, which composes it) made that message a form-level
  reason beside the reasons it joins: a refusal of one field showed the field's
  sentence under the field and again on the banner, and the banner took focus.
  Now the adapter keeps each field's reasons under its key, moves the non-field
  reasons (the `__all__` key) to the form level as they are, and uses the message
  only for a refusal that carries no reason at all, as `runActionResult` does; a
  refused target shows its reason, not the verb's summary. With field reasons
  only, focus goes to the first refused field in the dialog's order; where that
  field's control takes no focus, the banner, which names the field without
  repeating its reason, takes it.

  `ActionFormDialog` binds the reasons to the fields it renders for the values the
  form holds when the answer lands, so a reason under a hidden field, a field
  its `showWhen` hides or a field of an inactive schema
  branch shows on the banner, as `key: reason`, instead of being bound to a field
  nobody sees. `ActionFormDefinition.fieldNames` is removed and
  `jsonSchemaActionArgs` no longer returns it. `UseActionFormOptions.shownFieldNames`
  also takes a function of the form's values, read when the answer lands. A row's
  direct submit (`useDescriptorRowActions` with `valuesFromRow`) whose result is
  refused for its fields only toasts under the verb's label, not its id (the id
  still stands in when the label is not text).

  Upgrade: drop `fieldNames` from an `ActionFormDefinition`; nothing replaces it,
  and a reason under a name only its `content` editors hold now also shows on the
  banner as `key: reason`. A test that reads an adapted refusal whose message sits
  beside a field's reason expects no form-level message for it, and one that sends
  a non-field reason puts it under `__all__`.

- f3edb91: A relation field's `relationEdit: null`, and a related row held as its value,
  are honoured by every relation control a descriptor field renders through. Four
  defects stood in the way:

  - The dialogs render a relation field through their own control
    (`DescriptorFieldList`), which passed the picker neither the field's edit
    opt-out nor a known option. In a dialog `relationEdit: null` still offered
    Edit, and a field seeded with the related row (`{ id, <label field> }`) read
    the record again only to show its name. It now passes both, as the record
    form's field and the descriptor control do.
  - An action's relation argument could not declare the opt-out, although it
    renders through the same dialog control: `ActionRelationArg` now takes
    `relationEdit` from the descriptor's own declaration. An action's relation
    argument still holds a bare id (it is submitted as one), so its record is
    read for its label.
  - A registered widget did not receive the opt-out: the field handed to it by a
    form, a dialog or a line's cell left the member out. `relationEdit` is now
    declared on `FieldPresentation`, so a widget's `field` carries it, and a
    widget that renders a relation picker passes it on as the picker's `edit`.
    The party picker and the calendar picker do; the party picker also shows the
    label of a row it is given without reading the record.
  - `relationSelectedOption` made an option of any row with an id, labelled by
    the id when the row did not hold its label member, and a picker given an
    option reads nothing: a relation value such as `{ id }` showed its raw id. It
    now returns `undefined` when the row's label path reads `undefined`, so the
    picker reads the record for its label. A member held as `null` or blank still
    labels the option by its id, with no read.

  What else reads through `relationSelectedOption` follows the same rule: a
  multi-relation field reads the labels of such rows instead of showing their
  ids, and a board lane whose rows do not hold the lane relation's label shows the
  unknown-value label instead of an id.

  Not changed: a form spec (a JSON Schema form) cannot declare the opt-out; a
  descriptor field, an authored line field and an action's relation argument can.

- 37387b7: A theme may set `--font-family-display`, a display face for headings. It
  defaults to the sans family, so a theme that names only a body face changes
  nothing. The `display`, `title` and `heading` roles of `textRoleVariants`, and
  the card, dialog and popover titles, use it through the new `font-display`
  utility; their sizes and weights are unchanged.
- 5073340: A record form's fields are now the form's own and those of the groups it displays
  for the record. A group the form does not show no longer brings its fields into
  what the form checks and saves, nor among the fields its banner calls
  highlighted: a `savedOnly` group on a create, or a contributed section whose
  permission the record lacks (and every such section on a create, which has no
  record), as a row-decided container child the record does not admit already did
  not; nor does a record rail row behind its group's, its child's or its own
  permission that the record lacks. Before, a required field left empty in a
  `savedOnly` group on a create, in a contributed section whose permission the
  record lacked, or in such a rail row, blocked the save with nothing on screen; a
  `savedOnly` group's field was sent on a create, a text left empty as an empty
  text, and a create seed (`defaultValues`, a list's `createDefaults`) or a
  field's `defaultValue` for a field only such a group declares was sent too; and
  a server refusal of such a field was named among the highlighted fields. Now the
  save goes ahead without the field and sends nothing for it, and the banner still
  names the field, with the server's own text for its refusal, as `name: message`,
  without calling it highlighted; no control is marked invalid unless a page
  renders one for the field itself, which shows the message.

  The form keeps its own declarations and order: a field the page declares outside
  its own groups stays, with the page's declaration, whatever a contribution
  re-declares; a field of a displayed page group keeps the page's declaration; a
  field only contributed groups declare comes with the first displayed group's
  declaration, at that group's place; and a field of a page group the form does not
  display, which a displayed contribution also declares, comes with the
  contribution's declaration. Where the form displays every group, its fields are
  exactly what they were. A page's own field that only a contribution the form
  does not display re-declares now shows among the form's ungrouped fields, with
  its message under it when it fails a rule. Before, it showed nowhere unless it
  was the form's title, status or body, though it was still checked and saved: a
  required one left empty blocked the save with nothing on screen. Every group's
  section keeps its key, order and sequence; where that field is the page's only
  ungrouped one, the ungrouped section now comes in front of them. Besides that
  field and the rail row named under Limits, what a form shows changes only in its
  title, status and body, which are chosen among the record's form fields: a field
  only an undisplayed group declares is no longer one, on a create for a
  `savedOnly` group, on a record lacking a section's permission, and on an edit
  while its record loads, so a status or body declared only in a permission-gated
  section now appears when the record arrives.

  A control a page renders itself for a field only an undisplayed group declares
  is read-only, and nothing is sent for it, unless the form's own submit owner
  saves it (before, what was typed there was sent: a `savedOnly` group's field on a
  create, a permission-gated section's field on a create or on a record lacking
  the permission). Limits: a record rail row that
  shares its name with a field only an undisplayed section declares now decides its
  own visibility, where that section's declaration decided it.
  Upgrade: check every create seed and `defaultValue` for a field that only a
  `savedOnly` group or a permission-gated section declares, since it is no longer
  sent (declare the field where the create shows it, or let the server set it);
  and on a record whose form does not display such a group, its field is no
  longer a candidate for the form's title, status or body.

- 37387b7: An `EditableLineField` (an entry of `EditableLines`' `fields`, or of a form's
  `lineFields`) may declare `readOnlyWhen(row, parentRow)`: the field's cell is
  read-only on each row it returns true for, judged from the row and its owning
  document (`null` when there is none). The rest of the row stays editable and
  keeps its row actions, so a document's section or note row can lock, say, its
  product and price cells.
- f039872: A record form and a dialog now read the `required` flag of a field with a
  presence flag, a template or a bound, and of a member of an object field, as
  presence: such a field or member that is required and left out (absent, or a
  `null` it does not allow) now says "This field is required." under it. A record
  form on a create also counts the model's required-on-create names for such a
  field. Before, only a presence-required value, or one whose key was held with
  `undefined` or a `null` it does not allow, was refused; a required member that
  is not presence-required, as a hand-written FormSpec object's `required` makes
  it, was accepted, and a `MutationDialog` sent the object without it. An item of
  a list is always present, so no list item's verdict changes. The backend's own
  FormSpec projections mark every required property presence-required, so a form
  built from one refuses nothing new. A blank text, a text of blanks, an empty
  list and an empty object remain values, as before: the field's own bounds judge
  them, and an empty text under a minimum length of one, in a field neither
  omittable nor nullable, still says it is required. At the opening, a
  `MutationDialog` keeps Save enabled while a required member of an object field
  that is not itself required is left out, and shows the message under the member
  when Save is pressed; it disables Save, with no message, while such a member of
  a required object field, or a required field that may be left unset
  (`omittable`), is left out. An `ActionFormDialog` without a resolver disables its
  submit at the opening, with no message: a missing member's message shows once
  the person edits another member of its object field, and a submission that
  reaches the form anyway shows each message. A record form shows the message when
  the person saves. A dialog whose definition brings a resolver, such as the one
  `jsonSchemaActionArgs` returns, now refuses such a value before its resolver,
  with the message the resolver gave.

  The client no longer checks these values a person cannot see or change; the
  server answers for them, with its own message. A record form no longer checks a
  field whose descriptor is hidden, a field read-only as resolved for the form's
  values (by its own flag, its `resolve`, `createOnly` on an edit or `editOnly` on
  a create), nor a field outside the fields its save writes for the record (one in
  a section the record's implementation does not admit). Where such a field held a
  value the record form refused, the save used to stop, with nothing on screen for
  a hidden field or one outside those fields; a required field with a presence
  flag, a template or a bound that was not presence-required was not refused when
  left out or blank, and was saved as it was. On every surface, a member of an
  object field or an item of a list that is hidden or read-only is not checked,
  nor anything under it: one that is presence-required and left out, holds its key
  with `undefined` or a `null` it does not allow, or breaks a bound, an empty text
  under a minimum length of one included, was refused, a hidden one with nothing
  on screen, and is now accepted. A dialog reads a field's key holding `undefined`
  as absent, as a record form does, so a field that is bounded or templated,
  neither required, presence-required nor `omittable`, and opened that way no
  longer says it is required; a member holding its key with `undefined` still says
  it is required.

  Where the record form still checks, or no longer checks, a field against that
  rule: a field declared hidden whose control its page renders itself (with
  `BoundDescriptorField`) is no longer checked by the client, and a server refusal
  of it shows under that control and in the banner; and a control a page renders
  for a field outside the fields the save writes is no longer checked, and is
  read-only unless the form's own submit owner saves it, which receives what is
  typed there.

  A server refusal of a record-form field whose descriptor is hidden, that lies
  outside the fields the save writes, or that its condition hides now shows in the
  banner with its own text, as `name: message` under the field's raw name rather
  than its label, the way a refusal of an undeclared field does. Before, the
  banner said "Please fix the highlighted fields: …" with nothing highlighted and
  without the server's text, and next to a form-level error only that error
  showed. A shown field's refusal is unchanged, and a field declared hidden whose
  control its page renders shows the refusal under that control as well as in the
  banner. What a form saves or sends is unchanged.
  Upgrade: `structuredFieldErrors` returns `required` for a field left out at its
  own path that is required but not presence-required, where it returned nothing
  (its own `required` flag decides by default, or the new last parameter
  `required`), and for a required member left out that is not presence-required;
  it returns nothing at or under a member or list item that is hidden or
  read-only, where it reported that value's presence and bounds.

- d773a02: The keyboard focus ring is Tailwind's ring layer. `focus-ring` and
  `focus-ring-danger` write Tailwind's ring variable instead of a `box-shadow`
  of their own, so the one `box-shadow` Tailwind composes carries the ring and
  the element's own `shadow-*` elevation side by side.

  What a keyboard user sees:

  - In forced-colours mode (Windows High Contrast), every control that draws the
    ring shows a 2px outline in the system's colour, 2px outside it. The browser
    drops every `box-shadow` in that mode; in the last release no control that
    draws the ring showed focus there (controls with their own outline or border,
    such as the calendar's buttons, did). The outline is declared for forced
    colours only: normal mode paints no outline.
  - A focused control keeps its elevation shadow beside the ring: the active pill
    tab, the pressed segmented toggle, the drawer rail's tabs, a toast, the
    block-card link, an interactive card and a board or gallery card's frame.
  - An open tab panel, which Base UI puts in the tab order, shows the ring when
    the keyboard focuses it.
  - In the form's editable fields, a focused input, textarea or button directly
    inside the field drops its own ring for the field's; a control that draws a
    focus-within ring on an element of its own (number, search, JSON, markdown,
    tags) keeps it.

  The ring's colour, width and offset are the theme's `--ring` and
  `--ring-danger`, unchanged in name and value, whatever elevation a theme or a
  person chooses.

  An elevation token is a shadow, never `none`. A `shadow-*` utility composes
  `--elevation-*` into the same `box-shadow` as the ring, and a layer a browser
  cannot draw voids the whole list, ring included. "No elevation" is written
  `0 0 #0000` (the Ledger theme and the flat elevation choice now do). The theme
  contract holds every elevation, `--ring` and `--ring-danger` to a list of
  shadows in fixed forms, wherever a token layer is checked (a theme definition,
  its resolved options and a document's appearance): per layer, an optional
  `inset`, two to four lengths in `0`, `px`, `rem` or `em`, and an optional hex,
  `transparent`, `currentcolor`, `rgb()`/`rgba()`, `rgb(from … r g b / a)` or
  `color-mix(in …, … %, transparent)` colour. Anything else is refused, `none`,
  `0`, a named colour, `calc()` and other units included. `isShadowList` exports
  that judge and `THEME_ELEVATION_PRESETS` the elevation choices. A theme of
  yours that writes another value changes it to one of the forms.

  `@angee/app`: the composer refuses a theme stylesheet that declares an
  `--elevation-*` property, names one in `@property`, or holds a backslash, reading the text with its comments and strings, and names the
  theme; a theme sets its elevations in its definition, where the contract checks
  them. Other custom properties a theme stylesheet declares are untouched. An
  elevation set outside a theme (an addon's own stylesheet, the host's CSS, an
  inline style) is not checked. `assertThemeStylesheet`
  (`@angee/app/web-codegen`) is that check.

  Code of yours that removes a ring writes `ring-0`, and `outline-none` for
  forced colours, under a selector that outranks the ring's state variant:
  `shadow-none` clears an element's own shadow, never the ring, which is a layer
  of its own. An elevation written as a raw `box-shadow` (an arbitrary
  `[box-shadow:…]`, an inline style) competes with the ring: spell it with a
  `shadow-*` utility to keep both.

  The outline needs the `forced-colors` media query: a browser without
  `forced-colors` support shows no indicator in that mode.

- 63b200c: A refusal's form-level messages stay several. A save or a submit refused for
  more than one reason showed its reasons as one sentence: the form-level
  messages, and each message no rendered field claims, were joined with a space
  into one text, on the record form's banner and in both dialogs, and a screen
  reader heard one run-on string. The form-level error now holds a list, and
  `ErrorBanner` renders it: one message is the banner's body, several are a
  list, one item each.

  - `ErrorBanner` takes `messages`, a list of strings, in place of `description`.
  - `FormViewSaveSurface.saveErrors` replaces `saveError`, and an action form's
    `formErrors` replaces `formError`: each is the list of form-level messages,
    empty when there is none.
  - `formErrorsFromForm(errors)` reads that list from a form's errors
    (`root.server`), where `applyFormErrors` stores it; the error's `message`
    is the same messages, one per line.

  Upgrade: read `saveErrors` or `formErrors` and pass them to
  `<ErrorBanner messages={…} />`; code that tested `saveError` or `formError`
  for `null` tests the list's length. A test that matched a refusal's reasons as
  one sentence finds one list item per reason.

- 37387b7: `FormView`, and so `Form` and every registered form, takes an optional
  `renderUnavailable(id, unavailable)`: what a saved record's form shows when its
  id reads no record (a load error still shows its error), such as a redirect to
  the record that replaced it. `unavailable` is the default "Record unavailable"
  state, to render when there is nothing else to show. Without it the default
  shows, as before.
- 37387b7: A grouping level can show only repeated values: `repeated` on a `GroupSpec`
  ("Repeated values only", `?group=<field>:repeated` in the URL) makes the server
  group read keep only groups of more than one record the person can read and
  leave out the records with no value. It is offered where the level's axis can
  tell its empty group apart; a view that cannot read it natively drops the flag
  and keeps the grouping, while an authored default that asks for it there is
  refused as a query error. `@angee/metadata` adds `ResourceQuery.toGroupRead`,
  the `GroupRead` type, `groupAxisId` and `groupSpecsFrom`. A page's `bulkActions`
  receives `collection.selectedGroups`, the selection split by its innermost
  groups (`SelectedGroup`, `SelectedGroupLevel`), absent on an ungrouped list and
  `null` when a selected row cannot be placed. `Button` and `SelectionBarAction`
  take a `disabledReason`: a reason disables the verb, which stays focusable, and
  is its tooltip and accessible description; `null`, `false` and an empty string
  are no reason.
- 39bec0a: A grouped list orders its groups by their own axis. Before, a list grouped in the browser handed
  the table its whole order, so the table sorted the groups by every key of it: by the sum of a
  number column, by a group's first record's text, never by a date's month or day, and by the axis
  only when the order led with the axis's key. A list grouped on the server always asked for its
  groups ascending, so a press on its grouping column changed nothing a person saw. Now each level's
  groups go by label, then identity, in the direction the list's order holds for a key the axis
  shares or for the field it buckets, else ascending (`GroupAxis.direction`), the group with no
  value last in both directions, and on two levels the inner groups under an outer group with no
  value ordered as under any other: the table state hands the table each axis's key, ascending,
  ahead of the list's other keys, the axis's column comparing its groups in that direction, and the
  server's groups read asks for the same direction. The list stores, links and saves its order as
  before, with no key a person never chose, and URLs and saved views written before read as they
  did.

  What a person sees change: a browser list whose order does not lead with its axis now lists its
  groups by the axis (an order holding the key behind another, an order without it, a grouping
  chosen with "Group by", a date by month or by day, two levels whose order holds only the inner
  key); a server list whose order holds its group key descending lists its groups descending; a
  related record with no name comes after every name in both directions, in the browser as from the
  server. A column whose key a group axis shares, the grouping column, shows its groups' direction
  on its sort button and its arrow, never "not sorted", and its press turns them: ascending and
  descending in turn, never removed, the first press writing the key descending where the order did
  not hold it. Its header's `aria-sort` says "none", as an unsorted header's does: WAI-ARIA puts
  `aria-sort` on one header at a time, and on a grouped list that is the header of the key the
  records follow, which keeps it as before. A press on any other column, and a choice in the pane's
  Order select, orders the records inside the groups and keeps the axis keys the order holds, now
  behind the pressed key (`title:asc,status:asc`, where it was `status:asc,title:asc`). It does not
  keep the date a level of months, quarters or weeks follows, which is a key the records order by and
  no key the level shares: on a list grouped by month it turns the months back to ascending, as it
  did before; keeping that date is a later change. A press on a
  grouping column leaves the records' order alone: its key leads and every other key the order
  holds stays behind it, in their order, the records' and another level's, so the records inside
  the groups keep their order and the header of the key they follow keeps its `aria-sort`
  (`status:desc,rank:asc` from `status:asc,rank:asc` or from `rank:asc,status:asc`). It drops only
  the other keys its axis shares (a grouped relation's identity beside its name), and keeps its sort
  button, and the focus on it. A date's months, quarters and weeks follow the date they bucket:
  ordered by the date
  descending, the newest bucket comes first, its records newest first, and with the date's key
  cleared the buckets go ascending; the date's own header keeps its sort and its three-state press,
  since a bucket's records do not share their date. A list grouped by a field no read can sort by
  still lists its groups by their axis, and that field's header offers no press, and so shows no
  direction.

  **Changed API.** `@angee/ui` augments TanStack's `TableMeta` with `sorting` beside `groupAxes`:
  the list's order, which `ListHeaderCell`, a header's sort button, `listOrderChoice` and a press
  read, since the table's own `sorting` now leads with the axis keys, each ascending.
  `buildColumns` takes each axis's direction (`BuildColumnsOptions.directions`, by position beside
  `axes`) and gives the axis's column a comparator in that direction over each row's group key
  read once per sort (`GroupAxis.compareGroups`, `GroupAxis.groupKey`, the identity the table's own
  cached grouping value), `sortUndefined: false`, since the
  library's `"first"` and `"last"` never tie two missing values and its default decides one from
  the column's own value, and `enableSorting: true`, so the table always sorts its groups; it keeps
  the press the column's header had as `meta.pressable`, which its sort button reads. A list
  column's header is a stable component, so the columns built again when a group turns keep each
  header mounted. `ListHeaderCell`'s `aria-sort` says "none" on a column whose key a group axis
  shares, where its sort button shows the groups' direction. **Upgrade:** a table of your own that
  sets `meta.groupAxes` and hands the table a sorting other than the list's order sets
  `meta.sorting` to that order; one that hands it the list's order needs nothing. A table of your
  own that calls `buildColumns` with axes and sorts its groups descending passes their
  `directions` and hands each axis key ascending. A program that augments `TableMeta` must not name
  a key `sorting`. Code that reads a grouped list's URL or saved order after a press finds the
  pressed key first. Code that asks a `buildColumns` axis column's `getCanSort()` whether its header
  can be pressed reads `meta.pressable`. Code that reads a grouping column header's `aria-sort` as
  its groups' direction reads its sort button's name.

  Not in this change: an absent `groupStack` or `pageSize` in a saved view; the nexus inbox's own
  Order select; a client list in an inherited scope ignoring its `order`; the empty group's two
  labels ("No owner" from the server, "No value" in the browser).

- a6093ed: An addon may supply the deployment's appearance at run time through the new
  `AddonManifest.hostAppearance` key, a `HostAppearanceSource`. Like
  `dashboardStore` it is install-one: two installed providers fail composition
  with `Addons "a", "b" each provide the host appearance; install one.`, and
  `ComposedAddons.hostAppearance` holds the one source or `null`. `createApp`
  calls the source's `useOptions(theme, build)` hook once per render of its
  session frame, above every layout, and passes its `HostAppearanceRead`
  (`{ options, pending }`) to the appearance provider; where those options rank is
  the appearance guide's "Host and user precedence". `HostAppearanceDefaults`
  gains `pending`: while it is set, `AppearanceProvider` keeps each person's first
  paint, and the boot cache, where they wear the host's options (a person following
  the app default, or a locked app); a person's own saved theme or customisation is
  painted at once, and after the first paint every choice applies at once, a
  draft's included. Identity's own hold is unchanged. Only the session frame sets
  it: `CreateAppInput.appearance` is now `Omit<HostAppearanceDefaults, "pending">`.
  `AppearanceState.hostOptions` keeps its identity across renders that change
  nothing. A host with no source installed is unchanged.
- 42e57b0: `useImplConfigFields` takes the name of the model's JSON field that holds the
  chosen implementation's configuration (a fourth argument, `config` when left
  out), so a model that keeps it under another name, such as `target_config`,
  edits it field by field from the implementation's own schema as well.
- f3713fd: A record form's line cell shows its reasons as a record field does: the error
  the form holds at the cell's path (`lines.1.label`), whether the server refused
  the line there or the form's own check failed there. A cell whose widget
  reports its draft invalid (a JSON cell whose text does not parse) now stops
  Save at that cell: the cell is marked invalid, says the check's reason under
  it, takes focus, and the lines' pane is selected; nothing is sent until the
  draft parses. Before, `EditableLines` named that cell's control by its column
  alone, so the check held the draft under the bare name (Save did nothing a
  person could see, or marked a record field of the same name), and a line's
  cells showed only the server's reasons. The check follows its line as lines are
  added, duplicated, removed or reordered; a server reason does not, since the
  form drops every server reason under the lines on such an operation after a
  refused save, as before, while the form's own check runs again and its reasons
  return at each line's new index. A cell's reasons are worded from its line's
  errors by the walker a record field's go through, so a reason of the line
  itself (`lines.1`) is no cell's, whatever the columns are named, and a reason
  under a member of a cell's value (`lines.1.payload.inner`) keeps its path, as
  an object field's member's does; a reason describes the cell's control
  (`aria-describedby`); a column with a reason on any line, the server's or the
  check's, cannot be hidden; a server reason shows once, under its cell, except
  in a column named `ref`, where that walker never looks (a recorded defect, the
  same as a record field's member of that name), and several under one cell show
  as one line, as a record field's do. A `BoundDescriptorField` with a `scope` named its control by the
  scope-relative name in the same way and now names it by its whole path
  (`settings.payload`), where the check's reason is marked and shown.

  **Upgrade:** `EditableLines` no longer takes `rowErrors`; it reads each cell's
  reasons from the form it is given (`control`), so set them there at the cell's
  path (`setError("lines.1.label", …)`, or `useForm`'s `errors`).
  `lineRowErrorsFromDottedPaths`, `FormViewSurface.lineRowErrors` and the record
  form's `serverFieldErrors` are removed: read a field's errors from the form
  (`form.getFieldState(path).error`, `form.formState.errors`).

  A line's own reason (under `lines.1`, no cell) and a reason of the lines as a
  whole (`lines`) still show nowhere but in the banner's line naming the refused
  fields.

- 58eabd4: Edits made in a record form's lines are undo steps of the form's history: adding,
  removing, duplicating or reordering a line is one step each, and typing in a cell
  is another. `EditableLines` takes an optional `history` with the form's
  `perform`, `start` and `commit`; a record form passes its own, so its
  `onFieldInteractionStart` and `onFieldInteractionCommit` callbacks now also
  receive a line cell's path (`lines.2.label`). `useFormHistory`'s `perform` reads
  the form's values after its action, so a change that sends no values
  notification (a field-array append, insert, remove or move) is recorded too.
  `emptyLineRow` and `duplicateLineRow` now return a row that carries a client key
  of its own. Upgrade: code that compares a row built by `emptyLineRow` or
  `duplicateLineRow` with a literal allows for that key, and code that adds a line
  to a form with undo builds it with one of the two and then performs it.
- 37387b7: Scrolling a list renders only the rows that enter its window: its other rows,
  group headers and table header no longer re-render, and a resource view hands
  its surfaces the same filter, grouping and sorting until they change.
  `RecordRow` (from `@angee/ui/views/resource-view-list-body`) requires `cells`,
  the row's visible cells, and no longer reads them itself, so it never draws
  stale cells after a column is shown or hidden. `useVirtualWindow`'s item count
  and size estimate are optional and default to the virtualizer's own;
  `FlatListBody` now takes both from its `rowVirtualizer`. Upgrade: pass
  `cells={row.getVisibleCells()}` to every `RecordRow` you render.
- 37387b7: `List`, `ListView` and `ResourceList` take `relatedModels`, the model labels
  whose changes refresh the list: a change on one of them whose related records
  name any record of the list's model refetches it, including records outside the
  current page that may enter its filter. Use it for columns and filters derived
  from related rows; a list over an authored `source` declares its interests on its
  own query instead. The dialect hooks behind them (`useAngeeAggregate`,
  `useAngeeGroupBy`, `useAngeeGroupByBatch`, `useAngeeListBatch`) take the same
  option, and a list's own and related changes share one coalesced refresh.
- 37387b7: A theme or branding fragment can own the sign-in presentation by contributing
  one child to the new `auth.login#hero` container. Its content is a
  `LoginHeroContent` (`@angee/ui/runtime`): a `render` component, an optional
  `presentation: "full-bleed"` and an optional `backgroundImageUrl`. `LoginPage`
  renders it on every page that composes it unless an explicit `hero` prop is
  passed; a second contributor, or a child without a `render` component, fails at
  boot. A host's hero (the prop or a contribution) now renders at every width,
  stacked above the sign-in card on small screens; the framework's own hero still
  shows from `lg` up. `LoginPage` also takes `presentation="full-bleed"`, and its
  `presentation` and `backgroundImageUrl` serve only its own hero: a contributed
  hero brings its own. The sign-in URL is the declared path of the `auth.login`
  route, which the auth gates, the auth provider and `useLoginPath` read.
  Upgrade: remove `loginPath` from `createApp`'s input and declare the
  `auth.login` route at that path instead.
- 37387b7: `MessageHtmlFrame`, now exported, is the one sandbox for a message's HTML, and
  `MessagePartsView` renders through it: it runs no scripts, resolves `cid:`
  references to the URLs a caller gives it (`inlineUrls`), keeps remote images
  blocked until the reader chooses "Load remote images" (or `remoteImages` is
  set), and opens links in a new tab with no opener and no referrer. An HTML part
  in `MessagePartsView` therefore no longer loads remote images by default.
  `PreviewFileContent`, now exported, fetches a preview file and owns its loading
  and error states for any `read`; it aborts a pending fetch when the URL changes,
  and a read may register a release (`onCleanup`, on its `PreviewFileReadContext`)
  for what it creates. A preview provider may declare `maxSize`, so a larger file
  of known size falls through to the next matching provider
  (`resolvePreviewProvider` takes the size as a third argument).
- 2cc25ca: **Changed API.** `@angee/ui/lib/use-motion-tokens` is removed and
  `@angee/ui/lib/use-root-tokens` is added: `useMotionTokens` lives there now,
  beside `useRootTokens`, the one reader of the root's resolved tokens. Import
  `useMotionTokens` from `@angee/ui/lib/use-root-tokens`.

  `MessageHtmlFrame` shows when the keyboard is in a mail. The framed body is a
  tab stop of its own, whether or not the mail scrolls, so a mail with links gains
  one stop, the mail itself, before its first link. While the body or anything in
  the mail has keyboard focus, a ring stays on the frame's edges, in three tones
  of 2px each: the theme's `--border-focus` outside, then a black line, then a
  white one, so one of them holds contrast on any mail, whatever it paints and
  whatever the theme's color is (at least 4.6:1 on any grey). A click on the
  mail's text or on a link shows no ring
  until the next key press; a click into a field of the mail shows it, as the
  field shows its own. The
  framed document carries the frame's title. The ring's color is part of the
  framed document, so a theme, brand or light/dark switch reloads an open mail,
  which returns to its top and shows no ring until the next Tab.

  The ring needs `:has()` (Chrome 105, Safari 15.4, Firefox 121) and nothing
  newer; a browser without it draws none, as before.

- 37387b7: `MutationDialog` reports a failure once: a refusal bound to a field stays under
  that field with no fallback banner (`applyFormErrors` sets none while a rendered
  field holds the error), and every call the dialog makes for its caller
  (`validate`, `onSubmit`, `mutation`) receives the new `AuthoredMutateOptions`
  (`@angee/refine`), which turn refine's generic error toast off for that call.
  The dialog takes an authored `mutation` (with an optional `mutationResult`) as
  an alternative to `onSubmit`, so `MutationDialogProps` is now a union of the
  two. An authored mutate function takes the options as its second argument and
  `ActionMutate` as its third; an action's `submit(values, context, options)`,
  `useActionForm`'s `submit` and the dashboard store binding's `save` and
  `createPersonal` receive them as well. `createMutationDialogTestDouble` runs
  `validate` before it submits, as the dialog does. Upgrade: a `validate`,
  `onSubmit`, action `submit` or store binding that makes an authored call passes
  the options on to it (or hands that call to the dialog as `mutation`), so its
  failure is not toasted twice; code reading `onSubmit` from `MutationDialogProps`
  checks for it, since a dialog may carry `mutation` instead.
- 50f0cb4: A member of an object field (a FormSpec `object` widget) keeps its message. In a
  `MutationDialog`, which checks every change, a value that broke a member's rule
  disabled Save with no message on screen; in an `ActionFormDialog` and a record
  form, which check from the first submit on, the member's message showed after a
  refused submit and went at the next edit, though the value still failed. Now, in
  a record form, a `MutationDialog` and an `ActionFormDialog` (unless its
  definition brings a resolver other than the one `jsonSchemaActionArgs` returns),
  a member's message behaves as a top-level field's: a `MutationDialog` shows it
  as soon as the value fails, and these forms keep it through an edit that still
  fails and remove it when the value is corrected; correcting one of two failing
  members removes only that member's message. A form whose resolver is another one
  an action form definition brings, or `createJsonSchemaResolver` used directly,
  still loses a member's message at the next edit, and a member named `ref` shows
  no message in any form. Which rules fail and what a form saves or
  sends are unchanged. The error these forms' resolvers return for an object field
  (a field with an `objectTemplate`) whose members failed now carries a `type` at
  the field's own node, `MEMBER_ERRORS` (`"memberErrors"`), and no message of its
  own; `@angee/ui/views/validation-errors` exports it with `markMemberErrors`,
  which sets it from the fields a form shows. Code that compares such an error
  tree deeply expects that `type`.
- 640db7c: What a form field holds when it holds nothing is now decided by one rule, which
  the record form's draft and seeds, the action, mutation and schema dialogs and
  the presence control all read; the record form's own rule is gone. First match
  wins: a field that declares a `format` (a date, a date and time or a time
  carried as text) holds `null`; an explicit `string` kind holds `""`, whatever its widget; the number
  widgets (`integer`, `float`, `percent`, `progressBar`) and the `date`,
  `datetime` and `time` widgets hold `null`; the list widgets
  (`tagInput`, `list`, `rows`) and a relation list hold `[]`; the `switch` widget
  holds `false`, the `object` widget `{}`, and the `json` widget `[]` for an
  `array` kind, `null` for an untyped value and `{}` otherwise; then the kind
  decides (`array` `[]`, `object` `{}`, `boolean` or `switch` `false`, a number or an untyped
  value `null`, or `""` under the `select` widget); anything else holds `""`. A
  lines editor's new cell is not a form field and keeps its own rule. Not
  changed, and known: the `boolean`, `booleanToggle` and `booleanBadge` widgets
  and the `money` widget still hold `""` when no kind is declared, so an optional
  argument declared by one of them alone still reaches `submit` as `""`. A
  boolean waits for a field's type to reach its descriptor, where a kind decides
  it: holding `null`, the presence control would show an unchecked box with "Left
  empty", unlike the `switch` widget and a `boolean` kind, which hold `false`. The
  `money` widget, which the money addon's fragment registers, waits for a seam
  where a widget declares the kind of value it holds; the rule does not name it.

  What an untouched field seeds and sends, where that changed. A record form
  sends a structured field whole once one of its members changed, so "a sibling
  changed" below means an edit that changed another member of the same structured
  field, and the row holds for a member the record lacks that is neither
  omittable nor presence-required: a member the record holds goes as loaded, and
  the backend's form spec makes every member omittable or presence-required, so
  no member it emits reaches those rows today.

  | Field                                                                                                                           | Where                                                                                                 | Before | Now      |
  | ------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- | ------ | -------- |
  | a form spec `boolean`                                                                                                           | record form: a create; an edit, a sibling changed                                                     | `""`   | `false`  |
  | an `array` under the `json` widget                                                                                              | record form: the same two                                                                             | `{}`   | `[]`     |
  | an untyped value under the `json` widget                                                                                        | record form: a create                                                                                 | `{}`   | left out |
  |                                                                                                                                 | record form: an edit, a sibling changed                                                               | `{}`   | `null`   |
  | a number kind under a widget that is not a number's (`money`)                                                                   | record form: a create                                                                                 | `""`   | left out |
  |                                                                                                                                 | record form: an edit, a sibling changed                                                               | `""`   | `null`   |
  | a date, a time, or a date and time, carried as text with no declared `format` (a `string` kind with only a date or time widget) | record form: an edit, a sibling changed                                                               | `null` | `""`     |
  | the `date`, `time`, `integer` or `float` widget with no kind                                                                    | an action dialog's optional argument, at its `submit`; a `MutationDialog` field, at its `parseValues` | `""`   | `null`   |
  | the `datetime` widget with no kind                                                                                              | a `MutationDialog` field, at its `parseValues`                                                        | `""`   | `null`   |
  | the `json` widget with no kind                                                                                                  | an action dialog's optional argument; a `MutationDialog` field                                        | `""`   | `{}`     |
  | a relation list declared by its widget alone                                                                                    | a `MutationDialog` field, at its `parseValues`                                                        | `""`   | `[]`     |
  | a number that is neither required nor nullable                                                                                  | a schema dialog; a `MutationDialog`                                                                   | `null` | left out |
  | an integer under the `select` widget, not required                                                                              | a schema dialog; a `MutationDialog`                                                                   | `""`   | left out |

  A descriptor that declares a kind under a widget of another family follows the
  rule above and moves too: an object, a list or an untyped value declared under
  a text widget holds `{}`, `[]` or `null` (left out of a create) where it held
  `""`, and a text declared under a list or the `json` widget holds `""` where it
  held `[]` or `{}`. No other change was found over the pairs a form spec, the
  metadata layer or a hand-written descriptor produces.

  A field whose empty value is `null` (a number, a date or time, an untyped
  value) has no "present but empty" state: holding `null`, or nothing without a
  default to use, the presence control shows the field's own control with its
  hint ("Left empty", "Optional" or "Not set") and no "Set value". "Set value"
  stays for the other fields and writes their empty value; it no longer writes a
  `0` nobody chose for a number. Two step configurations of the framework show
  it: the extraction steps' `thinking`, holding its `null` default, shows its
  control with "Left empty" and "Omit value" where it showed a "Not set" button
  beside it; and the start-conversation step's `timeout`, holding nothing, shows
  its own control with "Optional" where it showed only "Not set" and a "Set
  value" that wrote `null`.

  A member that may be left out (it is not `required`) is no longer seeded with a
  value its own declared type refuses: it stays absent, in a schema dialog, a
  `MutationDialog` and a record form's create draft, through one check. A record
  form's edit does not read it yet. The seeding policies otherwise keep their
  differences: a schema dialog seeds a `nullable` member with `null` and an
  object with its template's seeds, a `MutationDialog` seeds `null` and `{}`, a
  record form neither.

  Upgrade: an action dialog's `submit` and a `MutationDialog`'s `parseValues` now
  receive `null` for an untouched date, time or number field declared by its
  widget alone (`date`, `datetime`, `time`, `integer`, `float`), `[]` for a
  relation list and `{}` for a `json` one, where they received `""`; code that
  tested for the empty text tests for `null`, or for a value. A `submit` that
  passed an untouched optional date on as it was now sends `null`, which a date
  parameter accepts, where it sent `""`, which it refuses. Upgrade: a test that
  pressed "Set value" on a number and expected `0` finds the number's own control
  instead.

- 37387b7: Pickers list options under headings by one rule, `optionGroups` (exported from
  `@angee/ui` and `@angee/ui/lib`): groups with a `groupOrder` first, by that
  order, then every other group, the ungrouped options included, in
  first-appearance order, with the ungrouped bucket headed "Other" only after a
  labelled group. `Select` (`SelectChoice` gains `groupOrder`), the search
  panel's filter sections and facets, and the to-many relation picker use it;
  `WidgetOption` and `RelationOption` gain `group` and `groupOrder`.
  `RelationMultiFieldWidget`, `Facet` and the relation option hooks
  (`useRelationOptions` and its siblings) take `optionGroupField` and
  `optionGroupOrderField`, related-row paths such as `category.name` and
  `category.position`, and list the options under the headings the related
  records name.
- 988b712: A pane list's Order select shows what its headers show and chooses as a header press
  does. Before, it checked the first key of the list's state: on a list grouped by status
  and ordered by status then rank it checked "Status, ascending" while the Rank header
  showed the sort, on a list whose order was inherited it checked nothing, and a choice set
  that column alone, so a list grouped in the browser lost its grouping key and reordered
  its groups. Now it checks the first key the rows follow, the one `headerSort` shows on its
  column, and a choice keeps the keys a group shares behind the chosen column, as a press
  does (`title:desc,status:asc`). It offers only keys that order the records: a column its
  header can sort, and not one whose key every group's records share (a grouping column's,
  which orders the groups; its own header turns them and shows their direction). Choosing what it
  already shows changes nothing. The list view's and the collection tree's panes both read
  it from their table.

  **Changed API.** `ResourceToolbarProps.sorting` and `ResourceToolbarProps.onSortingChange`
  are replaced by `order`, a `ListOrderChoice` (`{ shown?: { field, desc }, offers(field),
choose(field, desc) }`). `listOrderChoice` and `ListOrderChoice` are exported from
  `@angee/ui/views/resource-view-list-body`. **Upgrade:** a pane toolbar of your own over a
  TanStack table passes `order={listOrderChoice(table)}` from that entry.

- 37387b7: `PaneRow` takes `disabled` and `disabledReason`; a reason alone makes the row
  unavailable, and `null`, `false` and an empty string are no reason. An
  unavailable row stays listed, muted and without a hover fill, and neither
  navigates nor calls `onOpen`; it is a focusable button with `aria-disabled`,
  and its reason is shown in full under the title and announced as its
  description.
- 975d1f9: A field's `prefill` receives, as a third argument, the values the form was
  loaded with (in the field's scope). A preset that gives a sibling its loaded
  value back when the field returns to its loaded value leaves the form equal to
  what is stored, and so clean.
- 37387b7: A `PreviewFile` may carry `identity` (`{ id, version }`: the public file id and
  its content digest). `PreviewPane` keeps the URL it loaded, and its renderer
  stays mounted, while the identity stays the same, so a download URL that
  rotates its credential no longer reloads the preview, and a changed file or
  content loads the current URL; a file without `identity` is keyed by its URL as
  before. The new `usePreviewUrl(file)` gives any other renderer, such as a
  gallery image, the same rule.
- e7839e0: `@tanstack/react-query` must be at least 5.102.0, the release that adds
  `queryClient.query` and `infiniteQuery`. The app's route identity gate, auth
  provider and view-as identity read use them in place of the deprecated
  `ensureQueryData` and `fetchQuery`, so a host on an earlier 5.x release must
  upgrade it.
- 2f99820: **Changed API.** A record action bound with `useRecordActionMutation` is toasted
  by one owner, the action bar. The hook had two paths: by default it projected
  the outcome onto the rendered action's contract and the bar toasted it, a
  success under its message and a failure under the verb's label; with `settle`
  or `linkTo` it handed the outcome to the shared settle owner, which toasted it
  under the server's message. The second path and the `settle` option are
  removed.

  What changes on screen, for the verbs that passed `settle` or `linkTo`:

  - a refusal is titled by the verb's label, with the server's reasons (or its
    message) as the body, as every record action projected through
    `runActionResult` already was;
  - a transport failure is titled by the verb's label with the error's message as
    the body (it was titled by the error's message);
  - an answer that carries no outcome is titled by the verb's label with the
    shared "The action returned no result." as its body; a `noResultTitle` given
    through `settle` is no longer read;
  - the success toast appears after the navigation to the created record, the
    refresh and `afterSuccess`, not before them.

  And for every verb bound with the hook:

  - an answer that carries no outcome is now a danger toast, and the record
    neither refreshes nor runs `afterSuccess`. It was silent and did both; with
    `defaultMessage` it was told as a success;
  - `useRecordAction` now applies `defaultMessage` to an empty success message
    too, for every runner; it showed no toast, or an empty-titled one for a verb
    that settled.

  Not changed: a record action whose own `run` calls `useActionResultRun`,
  `useActionResultMutation` or `useRecordChromeActionMutation` is still toasted
  there, under the server's message (the proposal ceremony, round open and
  cancel, project lifecycle and creation, task promotion, trash and restore,
  account access, message delivery, triage, contributed conditional verbs and
  others). A transport failure still also raises the data layer's own error
  notification, as before.

  `linkTo` is unchanged: a verb that returns the record it created is followed
  there, through the same implementation the settle owner uses.

  Upgrade: remove `settle: true` and `settle: { … }` from
  `useRecordActionMutation` options. An inline `settle` is a type error; one
  passed through a variable or a spread still compiles and is ignored. A test
  that expected a refusal's toast to be titled by the server's message for such a
  verb finds the verb's label.

- f97b73c: A record form now decides in one place whether its save can write a field: of
  the record's form fields, those the model updates on an edit or takes on a
  create, and under the form's own submit owner, which receives the whole values,
  every field and every path. What the save sends, its resets of hidden fields
  included, reads that, as before; so now do the check, whether a field's control
  takes edits and whether a create displays the field. A field the save cannot write is no longer
  checked, whatever its widget, so a required one left empty no longer blocks the
  save, and its control is read-only and no longer marked required. Before, a
  field the model did not update showed an editable control on an edit, and a
  field outside the create fields one on a create, and the save dropped what was
  typed there without a word; a required one left empty blocked the save with its
  message under that control.

  Whether the record redacts a field is a second fact with one owner. A redacted
  field is not checked; its control is its lock, or read-only where a page renders
  the control itself, whatever its widget declares; and a server refusal of it
  shows in the banner as `name: message`. Before, a redacted required field left
  empty blocked the save with "This field is required." under its lock, where the
  person has no control. Redaction does not change what is sent: a redacted field
  the form itself wrote (a preset's write, a hidden reset) is sent as before, and
  one the form only loaded is not, since an edit sends only what changed. Limit,
  as before: a structured field goes whole once one of its members changed, so
  where the record redacts a structured field and a preset writes one member, the
  untouched members go as the placeholders the form loaded.

  On a create, a field the save cannot write that holds no value is no longer
  displayed at all: not in the sheet, its panes, the status or the body. Before,
  such a field showed an editable control whose input the save dropped or, where
  it was declared read-only, a bare label with an empty value (a stamp such as
  "Created at", a computed status, a number the server gives). A field that holds
  a value (a create seed, a list's `createDefaults`, a `defaultValue`, a preset's
  write) is displayed read-only, as soon as it holds one and for as long as it
  does; it is shown, not sent, since the create input does not take it. An edit
  displays every such field read-only, empty or not. A section left with no field
  to display shows nothing, and a pane left with none is no longer listed among
  the tabs, on a create and on an edit where a condition leaves it none. A pane
  the person had chosen that is dropped gives way to the first pane, and is
  selected again when it returns. A title the create cannot write is
  read-only and shows the create's own heading. An empty status that is read-only on a create although the create input
  takes it is not displayed either, as before (a proposal's state, a round's
  status, a version-control bridge's lifecycle).

  A control a page renders itself (`BoundDescriptorField`) for a path no form field
  declares, such as a field only a group the record does not display declares, is
  now read-only, where it took typing that the save dropped; the cure is to declare
  the field, hidden if need be, as pages that render their own control already do.
  Under the form's own submit owner it stays editable, and the owner receives what
  is typed there. Limit: such a control for a path the record redacts is read-only
  but shows no lock. The lock is `RecordFieldMarkControl`'s restricted mark, which
  the form's own field rows render in place of the control (`BoundFieldRow` in
  `form-view-body.tsx`); `LabeledDescriptorField`, which `BoundDescriptorField`
  renders, shows the field's marks without it. A server refusal of a field the
  save cannot write shows in the banner with its own text, as `name: message`,
  rather than calling the field highlighted. A record rail row whose field the
  metadata lacks or does not read, which was already not shown, is no longer
  checked nor saved either. What a form sends is unchanged.

  A registered widget declares `writesOwnValue: true` in its widget definition when,
  on a saved record, it commits its value through its own action, or not at all,
  and decides its own editability. Where the save cannot write the widget's field,
  a record form then leaves the widget enabled on a saved record; on a create the
  flag changes nothing, and a redacted field's control is read-only whatever the
  flag says. The flag is read from the registered definition, so such a widget is
  registered eagerly, not through `lazyWidget`. The shared `visibility` widget,
  `projects.phase` and `angee.tags.recordTags` declare it.

  Upgrade: a widget that writes by itself on a saved record (through its own
  action, mutation or dialog rather than the `onChange` it is given) must now
  declare `writesOwnValue: true`, or its control turns read-only on an edit where
  the save cannot write its field; the rule covers registered widgets only, and a
  component a form renders directly (an extra below the form, a group's custom
  content) is outside it. A control a page renders itself for a path no form field
  declares is now read-only unless the form's own submit owner saves it: declare
  the field, hidden if need be. A create no longer displays the empty read-only
  rows of fields outside the model's create input, nor a pane that holds only such
  rows; a `defaultValue` or a create seed keeps one displayed.

- 56fd4d4: A record form tells what its Save would write before a save. Its save surface,
  which a pane receives as `RecordPanelContext.form` and a toolbar or header as
  `RecordToolbarContext.form`, has `nextSave(): FormNextSave`, `{ data, lines }`,
  and the type is exported. Save and `nextSave` build it with one function, from
  the values, the baseline and the changed fields the form holds when it is
  called, so what a pane reads is what Save hands its writer.

  - `data` is the record's input, of the fields the save can write whose
    condition holds for the values, with the values Save writes for the fields a
    preset hid. On an edit, the fields that changed: a structured field the form
    saves as a whole goes whole, with its unchanged members, and where the form
    saves only some of its dotted members, only those go. On a create, the
    create input, less what it leaves out as blank.
  - `lines` is the lines diff of a form that edits its lines, its `payload` the
    whole list; `null` where the form edits none.
  - The writers: the native update sends `data`; the native create sends `data`
    with the `payload` under the lines field as a nested insert when the lines
    changed; an edit whose lines changed goes through the resource save, `data`
    as its patch and the `payload` as its lines; the form's own submit owner
    receives `data` and its context's `lines`, and may send something else.
  - On an edit where `data` holds no key and `lines` holds no change, Save still
    sends that empty update; what a server does with it is not the form's to know.

  Save and `nextSave` each read their own copy of the values: Save the one the
  form library's submit hands it, which keeps a class instance (and in a browser
  a file or a file list) as the same object; `nextSave` a `structuredClone`, which
  copies a class instance as a plain object. A record form's values are plain
  data, for which the two hold the same. What `nextSave` returns shares nothing
  with the form, and each call returns a new object.

  Reading it validates nothing, writes nothing, marks nothing dirty and starts no
  request, and it promises no more than what Save would build. That save may
  still not happen: the form takes no edit (it is read-only or its record has not
  arrived: Save throws), a save is already in flight (Save returns), or the rule
  check, a registered validation, lines another save changed, a running "view as"
  preview, a revision that is not available or the server stops it.

  The values, the baseline and the changed fields are read when `nextSave` is
  called; what the form saves (the record's fields and what the record permits,
  the mode, the model's metadata, the create seeds, the lines) comes from the
  render that made the function. The latest surface's is always right. One a
  reader keeps stays right across edits and discards, and across a save that
  leaves all of that as it was; once the record arrives, is replaced or permits
  something else, the reader calls the latest surface's. It calls it in render or
  after a pause, never synchronously inside a form subscription callback. A pane that
  follows the edits subscribes with `useFormViewValues` and compares what it
  reads by value, here as text, so that a debounce of it settles:

  ```tsx
  function UnsavedPreview({ recordId, form }: RecordPanelContext) {
    useFormViewValues(form); // re-render on each edit
    const unsaved = form.formIsDirty
      ? JSON.stringify(form.nextSave().data)
      : null;
    const [settled] = useDebounce(unsaved, 400); // from `use-debounce`
    // ...read the preview of `recordId` with `settled && JSON.parse(settled)`
  }
  ```

- 37387b7: A field's `prefill` receives the form's current values as its second argument,
  after the field's new value (this field's scope in a nested form), in record
  forms and in the dialogs that render a `DescriptorFieldList` (`MutationDialog`,
  `ActionFormDialog`), so a preset can derive its seeds from siblings, such as an
  All day toggle turning the bounds it shows into whole days. In a record form, a
  target a preset writes that the new values hide is dropped from the draft, and a
  dropped field that shows again takes its loaded value back. Saving an existing
  record calls `prefill` again for each visible preset field whose value changed,
  and writes its seed for every target that is hidden now and was loaded with a
  different value (a `prefillPreserveDirty` preset writes only the targets its
  `prefillReplace` names). The record form's rail shows a field exactly when the
  sheet would: visible for the current values (`showWhen`, through `resolve`) and
  not `hidden`. Upgrade: make every `prefill` a pure function of its arguments.
- 37387b7: Record forms and resource lists supply Archive / Unarchive, Move to trash /
  Restore and Delete themselves, from the resource's metadata, so a page no longer
  wires them. A page's `bulkActions` are added to these instead of replacing
  Delete, a bulk action runs on at most 25 rows a click (the rest stay selected),
  and Delete on a trashable resource is offered only for a row already in the
  trash. Archived rows leave default lists, dashboards and relation pickers unless
  a filter names the archive flag; every archivable list offers an "Archived"
  filter, and `useRelationPickerSearch` (now exported) and
  `useRelationPickerOptions` take `includeArchived` to list them. A list's row
  selection defaults on only when the list has lifecycle actions, `bulkActions`
  or a model that can be shared. A record verb marked `primary` now sits on the
  toolbar without a `placement`, on a saved record the form's band fills up to two
  buttons with its own unplaced, non-danger verbs, and an overflow (`⋮`) menu is
  always the last control of its group; `RecordActionBar` takes `fillBand`, `genericActions` and
  `contributedVerbs`, and `useTrashPrompt` an optional count. `@angee/metadata`
  exports `archiveFlagField`, `unarchivedFilter`, `resourceLifecycle` (with
  `ResourceLifecycle`), `canDeleteRecord` and `allowsRecordPermission`. Upgrade:
  replace `RecordActionBar`'s `menuPlacement="leading"` with `menuTrigger="icon"`
  (the trigger now follows the buttons) and drop `menuPlacement="trailing"`; stop
  importing `useTrashActions` and `useTrashRowActions`; drop a page's own Archive,
  Trash or Delete wiring and its `is_archived` filters; pass `selectable` to keep
  selection on a list that has none of those actions; pass `resource` (or
  `canRestore`) to `RemovedList` to keep its default Restore. `useBulkDelete` now
  takes the list's internal bulk runner in place of the clear-selection callback,
  so a page stops calling it and lets the list supply Delete.
- 90e0f2b: A record pane can name a field as the form does, and report its issues without
  interrupting. A pane that reports on a field (a refusal it shows outside the
  form) got the field's name and could focus it, but nothing offered the field's
  label, so two reports with the same words under two fields read as two
  identical lines. The form already owns the fact: the label its save banner
  names a refused field by.

  - `RecordPanelContext` gains `fieldLabel(name)` beside `focusField`: the text
    of the label the form shows for its field, or `undefined` for a field the
    form does not hold, shows without a label, or labels with something other
    than text.
  - `RecordIssues` gains an optional `fieldLabel` prop beside `onFocusField`: an
    issue that names a field is titled by that label, and the control that
    focuses the field is described by it (`aria-describedby`), so two controls
    with the same words tell a screen reader which field each goes to. Without
    the prop, or without a label, the issue shows its message alone, as before.
  - `RecordIssues` gains an optional `polite` prop, for issues about edits not
    yet submitted, which change while the person types elsewhere: the issues
    are then announced politely, a danger one included (otherwise an alert,
    which interrupts). The fragment renders one polite live region that stays
    in the page with no issues too, as an empty block, and the issues inside it
    carry no role of their own: a screen reader announces what arrives in a
    region it already knows. The tone and its colour do not change. No screen
    reader was run; the shape follows the ARIA practice for status messages.

  The save banner's line that names the refused fields reads the same lookup. A
  field whose label is not text is now named by its name there, where the line
  printed `[object Object]`; a label of blanks likewise; a text label is
  trimmed.

  **Upgrade:** code that builds a `RecordPanelContext` by hand (a test of a record
  tab's `render`) adds `fieldLabel`. A pane passes `context.fieldLabel` to
  `RecordIssues` as it passes `context.focusField`.

- 4bd5849: A refused save that carries a reason no field shows brings the person to the
  banner. A refusal may name a shown field and also carry a form-level reason;
  focus went to the field, the banner at the top of the sheet stayed out of view,
  and the form-level reason, the one that explains the rest, was not seen.

  - With a reason no field shows (a form-level one, or one of a field the form
    does not display), the banner takes focus, and it also carries the line that
    names the refused fields ("Please fix the highlighted fields: …"), which it
    showed only when there was no form-level reason. The one focused place says
    everything; each field keeps its own reason and its mark.
  - With field reasons only, the first shown field takes focus.

  `applyFormErrors` decides which, for a surface that passes it `focus` and a
  `fieldSummary`: the record form, `MutationDialog` and the typed-args action
  dialog. A surface that passes no `focus` moves none, and one that passes no
  `fieldSummary` shows no line naming the fields.
  The `focus` option, added in this release as `true`, is now the surface's way
  of bringing the person to the first refused field or to its banner: a function,
  the `focus` of the new `useRefusalFocus(messages, { form, ready })`, whose
  `banner` props are spread on the surface's `ErrorBanner`; `messages` is the
  list `formErrorsFromForm` reads. Both are exported. The record form composes
  them through its save surface (`saveBanner`).

  Upgrade: replace `focus: true` with the `focus` of `useRefusalFocus`, given the
  surface's form (and `ready: false` while its controls are locked), and spread
  its `banner` on the banner. A test of a refusal with both a field reason and a
  form-level reason finds the line naming the fields in the banner's list, and
  focus on the banner.

- 836268c: A dialog's refused select, combobox, date, time, tag input or relation picker
  takes focus, as a text field of a `MutationDialog` or of a typed-argument
  `ActionFormDialog` already did. The binder asked the form library to focus the
  first refused field from inside the submit, while the dialog's fields are
  locked: a select's or combobox's trigger is disabled, a date, time, tag input
  or relation picker shows its read view, and a schema dialog's fields sit in a
  disabled fieldset. The call reached a disabled or departed control, focus was
  lost, and the banner took it.

  - `useRefusalFocus` is now the one owner of where a refusal brings the person.
    `applyFormErrors` calls its `focus` with the first refused field
    (`focus(field)`) or with none for the banner (`focus()`); it no longer passes
    the form library's `shouldFocus`.
  - The hook takes the form, `useRefusalFocus(messages, { form, ready })`, waits
    until the surface reports its controls ready, then asks the form's own
    deferred `setFocus` for the field, and gives the banner focus when the field's
    control took none. `MutationDialog` and `useActionForm` pass
    `ready: !isSubmitting`; their fields still lock while they submit. The record
    form, whose fields never lock, passes no `ready`; where its refused save
    brings focus is unchanged. Not asked, the banner takes focus for a list it has
    not handled yet, never again for the same list still showing.
  - `formErrorsFromForm` reads one error object as one list, also for a form-level
    error that holds only a message, so the list is new exactly when an answer
    lands.
  - `@angee/ui/testing` exports `recordFocusCalls`: every `focus()` call, with
    whether its target could take focus by what the test DOM shows (not in the
    document, disabled, a form control in a disabled fieldset outside its first
    legend, inside `[hidden]`, `[inert]` or a closed `details`, a computed
    `display: none`, `visibility: hidden` or `collapse`, not focusable at all).
    The test DOM
    focuses most of these; a host hidden by an app stylesheet, which tests do not
    load, is a browser's to judge.

  Upgrade: nothing beyond what this release's note on `applyFormErrors`'s `focus`
  asks; the hook never shipped in another shape. A test that reads
  `document.activeElement` right after a refused submit waits for it: the field
  takes focus on the form library's timer. A test that read `shouldFocus` on a
  `setError` call reads the field `focus` was called with.

- 37387b7: A payload that a collection source's `select` refuses no longer throws in
  render: it becomes that read's error and is reported once per settle of the
  read, as a failed read is. `@angee/refine` exports `useReportRefusedReads`, the
  hook that does this reporting, beside `useDerivedBatch`; its facets and group
  reads already use it.
- 7aac88f: A refused save brings the person to its reason. A record form's banner sits at
  the top of its sheet; with the sheet scrolled down to the field being edited, a
  save the server refused showed nothing where the person was looking, and focus
  fell to the page because Save was disabled while it ran. Now, as the form's own
  required check already does:

  - the answer moves focus to the first shown field it names, in the form's
    order, which also scrolls it into view;
  - a refusal that names no shown field (a form-level reason, a field the form
    does not display) moves focus to the banner, which is focusable for that;
  - Save stays focusable while a save runs (`aria-disabled`, with `aria-busy`),
    so the keyboard user who pressed it keeps their place.

  `applyFormErrors` takes `focus` for the first two of these, the `focus` of
  `useRefusalFocus` (this release's notes on a refusal's focus give its shape);
  the record form's save passes it. An owner that projects issues while the
  person edits elsewhere does not.

  Upgrade: a test that expected Save to carry `disabled` while a save is pending
  finds `aria-disabled="true"`; a test that reads `document.activeElement` after
  a refused save finds the refused field or the banner.

- 4aed6db: A relation field can decline inline edit, and the quiet-call options of a
  surface that renders its own failures are public.

  - `relationEdit: null` on a field (a form's `Field`, a dialog's field
    descriptor, an authored line field) declines the relation picker's inline edit
    of the selected record, which is otherwise offered whenever the related
    resource has editable fields. `RelationFieldWidget` takes the same opt-out as
    `edit={null}`, beside `create={null}`. It is for a field where editing the
    related record is out of place, such as a channel chosen in a send dialog.
  - `SURFACE_RENDERS_FAILURE` is exported from `@angee/ui`: the call options
    (`{ errorNotification: false }`) a surface passes on when it shows a
    submission's failure itself, as the dialogs and the action form already do,
    so the failure is told once.

- 0b30a23: A field's `resolve` no longer returns `name`: the field keeps its declared name
  whatever `resolve` returns, and the type now says so
  (`Omit<this, "name"> & { name?: never }` on `FieldDescriptor`, so a dialog or
  FormSpec field's `resolve` may return only its own members). A returned value
  whose type carries a string `name` is a compile error. The fields
  `useImplConfigFields` returns have a `resolve` whose result no longer carries
  the prefixed name. Upgrade: delete `name` from what a `resolve` returns; take
  it out of a whole field spread there first
  (`const { name: _name, ...rest } = field`); annotate a helper that returned a
  descriptor type with that type's own result
  (`ReturnType<NonNullable<DescriptorField["resolve"]>>` for a dialog field's);
  do not cast a `resolve` result to a descriptor type or pass one where a
  descriptor is expected, lay it over the field instead
  (`{ ...field, ...field.resolve(values) }`); and read a `useImplConfigFields`
  field's `name` from the field, not from its `resolve`.
- 37387b7: A row action declared with `defineRowAction` titles its outcome toast by outcome:
  its `toast` (`RowActionToastCopy`) takes `successTitle` and `failureTitle` in
  place of the single `title`, so a refused verb no longer toasts the wording of a
  success. `toast.description` stays the failure reason when the server gives none.
  Upgrade: replace each row action's `toast.title` with a `successTitle` and a
  `failureTitle`.
- 89ea137: A saved or shipped view with no `sort` of its own keeps the order the list declares, and
  only an explicit `sort: null` clears it. Before, applying such a view cleared the list's
  order, while opening the same shipped view as a route's default kept the declared order,
  and the view showed active whatever the list was sorted by. Now the three agree:
  applying it sets the order the collection declares under the view's preset (the
  shipped view's own `sort`, else the list's declared order), opening its preset does the
  same, and it shows active while the list holds that order and stops when the person
  sorts otherwise; a view of the inherited order and an unordered view are never active
  together. A view saved while the list's view holds no order of its own stores no `sort`
  and keeps inheriting; a list mounted by `ListView` or `ResourceList` seeds its declared
  order, so a view saved there stores it.

  `ResourceViewContextValue` gains `inheritedSorting`, the order such a view keeps on that
  list, `undefined` when the list's view declares none; a hand-built context value supplies
  it.

  **Upgrade.** Four consequences, none needing code:

  - a shipped view without a `sort`, picked in the view switcher, keeps the list's declared
    order instead of leaving the list unsorted;
  - a view saved from a cleared order stores `sort: null`;
  - a view without a `sort` is active only while the list holds the inherited order;
  - a view saved before this change from a list whose order was cleared stored no `sort`,
    so it now restores the declared order: to keep it unordered, clear the order and save
    it again (it then stores `sort: null`).

- 37387b7: A list can let a person select everything its read matches: `ListView` and
  `List` take `selectAllMatching`, which offers "Select all N" where the pager
  counts what a verb acts on, and a page's `bulkActions` then receives
  `collection.allMatching` (`AllMatching`, `AllMatchingRecords` with the list's
  `where`, or `AllMatchingGroups` with the groups read, its order included).
  Opting in asserts that every page verb honours that mode or refuses it; the
  built-in Archive, Trash, Restore, Delete and Share refuse while it is on.
  `Checkbox` takes a `disabledReason`; from
  `@angee/ui/views/resource-view-list-body`, `SelectionToggle` takes a
  `disabledReason` and `ListSelectionBar` takes `matching`
  (`ListSelectionMatching`) and `deleteDisabledReason`. `AngeeListBatchEntry`
  from `@angee/refine` gains a required `updatedAt`, when the page last settled.
  Upgrade: code that builds an `AngeeListBatchEntry` by hand (a test double, say)
  sets `updatedAt`, `null` until the page has settled.
- 37387b7: An app built with `createApp` now recovers from a server outage on its own. A
  network failure or a 502, 503 or 504 answer keeps that request's ordinary error
  and starts one recovery check; only a failed check opens a connection dialog and
  stops further requests, and recovery refreshes the current route and the reads
  the outage refused. Failed mutations are never replayed: a write refused during
  the outage is reported once the server is back.

  `@angee/refine` exports `ServerConnection`, `ServerUnavailableError` and
  `ServerConnectionStatus`, and `createAngeeGraphQLClient` takes a `connection`
  option, the gate one app's schema clients share; `retryableQueryError` no longer
  retries a `ServerUnavailableError`. An addon replaces the dialog's artwork or adds
  guidance through the new `connection.lost#artwork` and `connection.lost#help`
  containers. `@angee/ui` exports `AnimatedThemeLogo`, which the dialog and the
  sign-in page use, and carries the dialog's `connection.*` copy. A host needs no
  change.

- 37387b7: `readSharedIdentity(queryClient, authProvider)` (`@angee/app/providers/auth`)
  reads the shared identity entry and reuses its last answer, as the route gate and
  the auth provider's `check` and `getPermissions` do. `ActionFormDialog` enforces
  the required fields of an action's form definition, static or computed from the
  values: a missing field shows its error and the submit button stays disabled
  until it is filled, while a definition with its own resolver keeps its own value
  checks.
- be22b32: The fields a form shows have one name where a refusal is bound: the option of
  `applyFormErrors` that lists them is `shownFieldNames` (on a descriptor form,
  the names of what `shownFields` returns), and `useActionForm` takes it under the
  same name and requires it. A reason under a path the list does not hold shows on
  the banner as `path: reason`; `[]` states that the form shows no field a reason
  can sit under. An inline action form that passed no list sent every reason to
  its banner as `key: reason`, even a reason a field it draws already showed under
  that field; one that states the fields it shows has each reason said once, at
  its field, and a refusal brings focus to its first refused field or, once it
  spreads the hook's `banner` on its banner, to the banner. `useDottedPathFieldErrors`
  names its parameter the same way. `useController` is exported beside
  `useWatch`, so an inline form whose control is not a native input (a select, a
  date popover) hands the form that control as its focus target.

  Upgrade: rename `fieldNames` to `shownFieldNames` on `applyFormErrors` and
  `useActionForm`; a `useActionForm` caller that passed none passes the fields its
  form shows, or `[]`, since an omitted list no longer compiles. An
  `applyFormErrors` call without a list keeps every reason at its path and no
  field's reason on the banner, for a caller that knows no drawn fields:
  `createJsonSchemaResolver`'s errors and the workflow studio's projection of its
  issues.

  `useActionForm` reads its list, and `fieldSummary` with it, when the answer
  lands, a plain list as a function alike: a field drawn or gone while the submit
  was out is claimed, or left to the banner, as the form shows it then.

- e3cb0a0: A list header shows a sort only for the key its rows visibly follow: the first
  key of the list's order that its groups do not share, and on a grouping column
  the direction its groups go in. A declared or default
  order may hold several keys, the later ones only breaking ties, and every one of
  them that has a column showed as sorted: a list grouped by kind and ordered by
  kind, key and language showed "Language, ascending" over rows not in language
  order, and the first press on that header sorted it descending. Now that first
  key's header shows its direction and a later key's shows "not sorted"; the header
  of a key every record of a group shares shows the direction its groups go in (the
  note on groups following their axis). A group's records share
  the value of a field the list groups by its whole value, a `Date` grouped by day
  included, and the members of a relation it groups by (`owner.name` under
  `owner`). A `DateTime` grouped by day, or a date grouped by month, still shows
  the date's sort, since one group holds many of its values. The new
  `GroupAxis.shares` in `@angee/metadata` answers which keys one group's records
  share, and a header's sort button and its `aria-sort` read that one rule: on a
  grouping column the button and its arrow show the groups' direction and the
  `aria-sort` says "none", since WAI-ARIA puts `aria-sort` on one header at a time,
  the key the rows follow.

  A press on a column that only breaks ties sorts it in its first direction
  (ascending, for a list's columns) as the only key the rows follow, the keys a
  group of the list shares, which no press drops, kept behind it, so every other key
  goes; the table's toggle would have turned one held ascending to descending. The
  press is decided from the list's live order when it happens: while
  a read is in flight the list shows the headers of its last settled read, and a
  second press there is the toggle's next step, not the first press again. Every
  other press is the table's own toggle, those keys kept behind it: on a header that shows a sort
  and on a column the order does not hold; a grouping column's press turns its groups between
  ascending and descending. On a list grouped in the browser, a grouping column's key
  orders the groups, and its press turns them: `[status asc, rank asc]` grouped by
  status becomes `[status desc, rank asc]`, and the open group comes before the
  closed one, its records still by rank.
  On a server-grouped list the same press sets the same order, and the groups read
  asks for the groups in that direction, so they turn too (the note on groups
  following their axis); it
  asks for each group's records in `status desc, rank asc`, so they keep following
  rank, and the Rank header keeps its sort.

  A press on the key the rows follow, when it is the order's last, turns it, the
  grouping key kept behind it, and the URL and a saved view keep the whole order it leaves.

  What does not change: the list's order holds every key, so every read
  asks for each key and a list sorted in the browser keeps its tie-breakers; no
  list reads or orders its rows differently until a person presses a header.

  **Changed API.** `ariaSortForColumn` is removed from
  `@angee/ui/views/resource-view-list-body`. **Upgrade:** a custom header renders
  its cells with `ListHeaderCell` from the same entry, which sets `aria-sort` by
  the rule above; no public function answers it alone. On a table of your own
  `ListHeaderCell` leaves out the keys a group shares only when that table sets
  `meta.groupAxes`; without it the header shows the table's first sort key.
  `@angee/metadata` gains `GroupAxis.shares(field)`, and each list's TanStack
  table carries the list's group axes as `meta.groupAxes`, which the headers read.

  `@angee/ui` now augments TanStack's `TableMeta` with the optional `groupAxes`.
  A program that imports `@angee/ui` and passes `meta` to a `useReactTable` of its
  own declares its keys by augmenting `TableMeta` too (an undeclared key no longer
  compiles), and must not name one of them `groupAxes`.

- e7fe5c9: An addon web package can declare a standalone page in `web/package.json`
  `angee.pages`: an HTML entry, the URL paths it answers and the media type a
  request asks for it with. `defineAngeeWebViteConfig` builds each declared page
  as `<id>.html` beside `index.html`, serves it in development at its paths to
  a `GET` or `HEAD` whose `Accept` header matches the manifest's `pageAccept`
  pattern, and serves the built file the same way in `vite preview` (the build
  describes its pages in `.vite/angee-pages.json` inside the distribution).
  `generateWebRuntime` returns the resolved pages and the manifest's `pageCsp`,
  and fails when a declared entry is missing; the build fails when a declared
  page is not emitted, or when its document references `../` or uses `srcset`.
  A built page's document also preloads the modules its entry imports
  dynamically (`<link rel="modulepreload">`), which Vite does only for static
  imports.

  A page's document is given the design tokens and the installed themes as
  stylesheets (`@angee/ui/tokens.css`, a new export of the token sheet alone, and
  `runtime/web/themes.css`), not the host's own stylesheet. It carries no content
  security policy of its own: the ingress sends the manifest's `pageCsp` as a
  header, preview does the same, and development writes it into the document
  after Vite's own head scripts. A manifest that declares a page but publishes no
  `pageCsp` (or an empty one) fails `generateWebRuntime`, and with it the build
  and the dev server. A host needs no change.

  `@angee/ui/theme-runtime` exports `resolveAppearance`, the one resolution of
  what a document wears from the build's appearance payload (the host's theme,
  its forced or system color scheme and its token layers, or a named installed
  theme), which `bootstrapAppearance` now uses, and `readAppearancePayload` for a
  page, which is given that payload as a JSON data block in place of the inline
  first-paint script.

- 37387b7: `SwitchRow` takes `frame`: `"inset"`, the default, draws the bordered row it
  always drew, and `"flush"` drops its own border, background and padding inside a
  host that already frames it. Existing rows look the same.
- 37387b7: `TextDiff` shows how one text became another, word by word: removals struck
  through and additions underlined, each with a visually hidden "Removed:" or
  "Added:" cue. It takes a `comparison` made once by `compareText(before, after)`
  (a `TextComparison` of `TextChange` runs), which two `TextDiff`s of a
  comparison pair can share, one per `side` (`"before"`, `"after"`); without
  `side` it shows both inline. A comparison that runs past its time budget is
  shown as a whole replacement with a note. `textDiffVariants` is its style
  recipe. `@angee/ui` now depends on `diff`.
- d18d3ae: A customizable theme may decline the run-time host appearance source:
  `createThemeCustomizationOptions(defaults, { version, acceptsHostAppearanceSource: false })`
  sets the new optional `ThemeOptionsDefinition.acceptsHostAppearanceSource`
  (default true), and `defineTheme` refuses a value that is not a boolean. While the
  host theme declines, `createApp` asks the `hostAppearance` source about no theme and
  no build options; whenever it asks about no theme, or about a theme without options,
  it wears none of the answer, options or `pending`, so the build's options for its own
  theme apply whole. The flag is not part of the options envelope or of the theme
  catalogue: no options version changes, and saved options resolve to the same tokens.
- 8c594e0: A customised theme's link, and each tone's text (`--brand-soft-text`,
  `--accent-soft-text` and the four status texts), now read at 4.5:1 on their own
  fills and on every surface of their scheme (canvas, sheets, popover and inset),
  in light and dark, wherever any colour can; where the surfaces pull opposite
  ways and none can, the text is the colour that comes nearest. The link was
  worked out against plain white and the dark ink, so it could fall under that on
  a customisation's sheets, most in dark. Texts move toward the ink in hundredths,
  so they keep more of their colour. A surface the customisation leaves at its
  default counts as the theme's definition states it, else as its defaults derive
  it, with a 1% margin: `ThemeOptionsDefinition.resolve` gains an optional second
  argument, the definition's own token layers, which `resolveThemeOptions` passes.
  A text is worked out only when its own input changes, so changing only surfaces
  keeps the theme's own link and tone texts. A theme's own colours are unchanged.
  The theme runtime feeds the theme catalogue's fingerprint, so each device drops
  its first-paint cache once, on its first visit after the upgrade.
- 37387b7: Time-of-day fields (the GraphQL `Time` scalar, `HH:MM[:SS]` with no zone) can
  be shown and edited. `@angee/metadata` gives a `Time` field the new `time`
  widget and filter field type (`ResourceFilterFieldType` gains `"time"`), and
  its client-side filtering and sorting compare clocks as SQL does (`09:30`
  equals `09:30:00`). `@angee/ui` registers the `time` widget, which edits
  through the browser's time control and stores a cleared time as `null`;
  `formatTime` and `formatTimeInput` read a `Time` value as written, with no
  zone; the filter editor offers a `time` field type. The generated web types map
  `Time` to `string`.
- e2df484: A number shown as a percentage or a progress bar no longer holds the empty text
  when it holds nothing. The `percent` and `progressBar` widgets join the number
  widgets (`NUMBER_WIDGETS`, beside `integer` and `float`) in the one rule for a
  form field's empty value (`emptyValueForField`) and in every reader of that set,
  so they behave as `integer` and `float` do: with no `string` kind they hold
  `null`, a record form's edit sends one holding the empty text as `null` and a
  create leaves it out, and the presence control treats them as numbers. No
  control of theirs writes the empty text: `percent` only shows a value, and a
  cleared progress bar writes `0`. The rows below hold with or without a bound:
  the rule check takes the `null` an optional field is seeded with for the field
  holding nothing.

  | A field declared by `percent` or `progressBar` with no kind | Where                                                                                   | Before                                         | Now                                                                      |
  | ----------------------------------------------------------- | --------------------------------------------------------------------------------------- | ---------------------------------------------- | ------------------------------------------------------------------------ |
  | untouched                                                   | its seed: a record form's create draft, a `MutationDialog`, an action dialog's argument | `""`                                           | `null`                                                                   |
  | untouched, on a field its metadata does not know            | a record form's create                                                                  | sent as `""`                                   | left out: it holds `null`                                                |
  | holding `""`                                                | a record form's create                                                                  | sent as `""`                                   | left out: the rule that sends a cleared number as `null` makes it `null` |
  | holding `""` (a stored value)                               | a record form's edit that sends it                                                      | `""`                                           | `null`                                                                   |
  | a member the record lacks                                   | a record form's edit that writes its object                                             | `""`                                           | `null`                                                                   |
  | omittable, holding nothing                                  | the presence control                                                                    | "Not set" and "Set value", which wrote `""`    | its own control and "Optional"                                           |
  | omittable with a `null` default, holding nothing            | the presence control                                                                    | "Not set" and "Use default"                    | its own control and "Optional"                                           |
  | nullable, holding `null`                                    | the presence control                                                                    | "Left empty" and "Set value", which wrote `""` | its own control and "Left empty"                                         |
  | nullable and not omittable, holding nothing                 | the presence control                                                                    | "Not set", "Set value" and "Leave empty"       | its own control, "Not set" and "Leave empty"                             |
  | nullable and omittable, holding nothing                     | the presence control                                                                    | "Not set", "Set value" and "Leave empty"       | its own control, "Optional" and "Leave empty"                            |

  In the presence rows, "its own control" is the progress bar's: a `percent` has
  no control (it only shows a value), so only the words and verbs beside it show.

  The same holds for a kind that decides nothing (one that names a widget, as
  `text` or `select` do) under these two widgets, and for a field whose kind
  names one of them with no widget, except the row for a `null` default: the
  presence control's compact rule reads the declared widget only, so that field
  still shows "Not set" and "Use default". The rows for the number and date widgets are
  read before the kinds, so a kind under `percent` or `progressBar` now follows
  them as it already did under `integer` and `float`: a `boolean` or `switch`
  kind holds `null` where it held `false`, an `object` kind `null` where it held
  `{}`, and an `array` kind `null` where it held `[]` (eight cells, the two
  widgets by these four kinds); a required one holding `null` is then missing,
  and for the `boolean`, `object` and `array` kinds the refusal check
  (`refusesSeed`) leaves such a member that may be left out absent. A `string`, `integer`,
  `number` or `any` kind keeps its empty value, but its empty text is sent as
  these widgets send it: `null` on an edit and left out of a create, where it was
  sent as `""`; a `string` kind is left out of an untouched create, and an
  omittable `string` or `any` kind with a `null` default shows its own control.
  No declared descriptor of the framework or of its products has one of these
  pairs, or either widget on a form.

  Not changed, and known: a progress bar shows `0` while it holds `null`. A
  `percent` declared required cannot be filled, having no control: a dialog's
  submit stays disabled for it, where a bounded one submitted `""` before (one
  without a bound was already refused). The
  `boolean`, `booleanToggle` and `booleanBadge` widgets and the `money` widget
  still hold `""` with no kind: a boolean waits for a field's type to reach its
  descriptor, where a kind decides it; `money`, which the money addon's fragment
  registers, waits for a seam where a widget declares the kind of value it holds,
  and the rule does not name it; `EditableLines` keeps its own list of number
  widgets for alignment.

  Upgrade: a `submit` or a `parseValues` that tested an untouched field declared
  by `percent` or `progressBar` alone for the empty text tests for `null`.

- 18ee144: A list keeps its whole sort order in the URL and in a saved view. Before, both kept
  only the first key: on a list grouped by status and ordered by status, then rank, a
  press on Rank turned it descending, the URL wrote `sort=status:asc`, and the list
  read back status alone, so Rank showed "not sorted" and the order was lost on reload,
  on a shared link and in a saved view; a saved view of a list ordered by rank and then
  title restored rank alone and did not show active right after it was saved.

  The URL's `sort` now holds every key, in order, as a comma list: an order of status, then
  rank descending, is `sort=status:asc,rank:desc`. On that grouped list a press on Rank now
  writes the pressed key first and the group's key behind it, `sort=rank:desc,status:asc`,
  and a reload reads both back. A one-key link reads exactly as before. A link whose
  order cannot be read (`rank:asc,`, `rank:asc:title`, a field the resource cannot sort
  by, or a field named twice) reads nothing and offers the list's reset, as an unreadable
  key always has.

  **Changed contract.** A saved view's `sort` (`ResourceViewFavorite`, and so a shipped
  `ResourceViewPreset`) and `ResourceViewInitialState.sort` are one `{ field, dir }` or a
  list of them; `null` still clears. A saved view is written with a list only when its
  order has several keys, so every one-key saved view and shipped view is stored exactly
  as before. **Upgrade:** code that reads `sort.field` from a saved or shipped view
  handles a list (`Array.isArray(sort)`); a declaration that names one key needs no
  change.

  The favorites preference document (`resource-view.favorites`) is now version 2. A
  version 1 document reads as before and is written as version 2 at its next change. A
  tab still running an earlier bundle reads a version 2 document as an unknown version
  once it receives the version 2 document (a live update or a reload): it then shows no
  saved views and writes none until it runs the new bundle, so it never drops a view whose
  order it cannot read.

- 7214202: A write from a dialog or a composer is safe to retry, and a dialog keeps a
  pending write and typed values.

  - `useIdempotencyKeys()` (`@angee/ui`, beside `createClientKey`) keeps one
    idempotency key per draft of a keyed write, for the app runtime:
    `send(scope, fire, outcome?)` and `discard(scope)`. A draft is named by its
    scope (the surface and what it writes to); its key is minted at its first
    attempt and lasts until a confirmed answer ends it, or the person discards it
    with nothing possibly written (no attempt lost, none on its way). No error
    releases it, so a resend, edited or not, is written once by the server, which
    replays the same content and answers `CREATION_KEY_CONFLICT` for other
    content under the spent key: the draft's next send then starts a new draft,
    and the caller only says what happened. `useIdempotencyKeyState(scope)` reads
    what the draft can tell (`{ outcome, uncertain }`), re-rendering only its
    reader. The store lives on the root `AppRuntimeProvider`, is forgotten on a
    change of signed-in person (not on entering a preview) and is never
    persisted, so a retry writes once from this page only. The record form's
    create, chatter's post, knowledge's note and vault clone, the project from a
    template, the dashboards' create and duplicate, and the workflow start sit on
    it; chatter and notes read again after a conflict, so the earlier version
    shows. A keyed surface still open when its answer is lost says so, that the
    write may have gone through and that sending again from the page sends it
    once (`formSubmitError`'s `answerLost`; chatter and notes in their own
    words). The record form promises it only where its own create root takes the
    key: a custom create owner (`submit`) receives the draft's key, but its lost
    answer says only that it may have been sent, since nothing tells the form
    the owner sends it. The dashboards' create and duplicate tell a spent key in
    their own words. The relation picker's quick create ("Create “name”") sits on it too
    when its create root takes a key, as one draft per relation field, so a
    retry after a lost answer no longer writes the record twice
    (`useRelationQuickCreate` takes the field's name); it tells a lost answer
    and a spent key in its own words, and any other failure through the data
    layer's notice as before.
  - One draft scope belongs to one surface at a time. `useIdempotencyDraftOwner(scope, holder)`
    holds it while the surface is mounted (an open keyed dialog, a composer that
    can post, and every create form, keyed or not), and in development two
    surfaces holding one scope throw at their next render, naming the scope and
    both holders, so two create forms of one resource mounted at once, neither
    nested under the other, fail there; a production build logs it once per
    scope and goes on. A strict-mode remount or a form replacing its predecessor
    in one commit holds it once.
    `useDraftScope(own)` names a surface's scope under the one its host provides
    (`DraftScopeProvider`): the record form provides its own
    (`record.create:<provider>:<resource>`, or `record.edit:…:<id>`), and a
    relation field's dialog its `relation:<field>` slot (`fieldName` on
    `RelationPicker`, `RelationFieldWidget` and `RelationMultiFieldWidget`;
    every form passes it, and a lines editor `<lines>.<row>.<column>`, naming
    the row by its id or client key), so a create form opened from another form,
    down any chain, or from another relation field or row to the same resource,
    never shares its draft.
  - `MutationDialog` with `idempotencyScope` (`KeyedMutationDialogProps`) calls
    its submission with the draft's key as a required `string` third argument;
    without one (`MutationDialogProps`, as before) it is called with none.
    `submitMutationDialog` takes the dialog's draft (`MutationDialogDraft`) and a
    keyed one's key. An action takes `idempotencyScope`, which may
    answer `null` when the context names no draft (no record to act on), and its
    `submit` reads `context.idempotencyKey`.
  - `FormSubmitResult`'s conflict carries the server's optional `code`
    (`FormSubmitConflictCode`: `STALE_REVISION` or `CREATION_KEY_CONFLICT`), set
    by `formSubmitError` and `actionOutcomeSubmitResult`; a bare conflict stays
    a stale one. `formSubmitOutcome(result)` says what a submit's result tells
    its draft's key, and the record form and both dialogs pass it to `send`, so
    a submission may return its conflict as well as throw it. A lost answer's
    result from `formSubmitError` is marked `outcome: "unconfirmed"`, given its
    sentence or not, so a returned one keeps its draft as a thrown one does,
    past a dismissal. A creation-key
    conflict does not lock a dialog's draft as a stale one does: the next
    attempt is a new draft. `@angee/refine`'s `ActionOutcome` takes a
    refusal's `code`, set by the caller that builds the outcome: the generated
    action documents select none.
  - The record form tells a create whose key an earlier attempt spent in its
    own words ("Created earlier": an earlier attempt from this form already
    created a record, to find among the existing ones before saving again), with
    nothing to undo; the save surface's `saveKeyConflict` says it is so, and a
    stale revision keeps its banner and its reload. Both form dialogs bring the
    person to a refusal, as the record form does: `MutationDialog` and the
    typed-args action dialog pass `applyFormErrors` their banner's focus
    (`useRefusalFocus`) and a line naming the refused fields
    (`fieldValidationSummary`); `useActionForm` takes `fieldSummary` and returns
    `banner` to spread on its `ErrorBanner`.
  - `@angee/refine`: `errorFromAuthoredEnvelope` (and so `useAuthoredMutation`'s
    `errorFrom`) throws an `AuthoredEnvelopeError` carrying the envelope's `code`,
    where it threw a plain `Error`. `boundedGraphQLTransportError` keeps
    `response.errors` only when the server's response held a GraphQL errors
    list: a network failure, a cancelled request or a gateway's own page carries
    none.
  - `failedWriteOutcome(error)` (`@angee/refine`) says what a failed write's
    error tells: `not-sent`, `unconfirmed`, `refused`, `denied` or `conflict`.
  - `DialogForm` takes `pending`, refusing every dismissal (Escape, the backdrop,
    Close, Cancel) while a submission runs: the dialog is `aria-busy`, Close and
    a footer Cancel stay focusable (`aria-disabled`) with their reason as their
    description, and a refused Escape or backdrop press is announced. Given
    `dirty` (true or false) it guards its work from the moment it opens: a
    dismissal of typed values asks first, with the shared unsaved-changes
    question, and a route move asks while it holds typed values or a running
    submission (while a submission runs the question says it is still sending).
    `holdsWork`, read live, lets an owner say it holds nothing, as after an
    accepted submit that moves on. Its `footer` may be a function of
    `{ dismiss, dismissRefusal }`, for a Cancel.
  - Every `MutationDialog` and every typed-args `ActionFormDialog` (a record or
    row action with `args`) now behaves so, and neither asks after an accepted
    submit whose `onSubmitted` or `onSucceeded` navigates. An answer that lands
    after the dialog went away is still reported (`useLateAnswerReport`, now
    exported with its `LateAnswer` type, for any surface whose answer can land
    after it is gone): an accepted one toasts its message, a refused one its
    reasons, a lost one that it may not have been sent. A `trigger`-owned `MutationDialog` given no
    trigger while open stays open until it closes.
  - The unsaved-changes guard's question says a submission is still sending when
    one runs (`pendingNow`), and the guard throws at render outside the router
    (Upgrade, below).
  - `MessageActions` shows its actions where the primary pointer is coarse
    (`pointer-coarse:opacity-100`), as touch never hovers.
  - `useUnsavedChangesNavigationGuard` takes `leaves(move)`, which says whether a
    route move abandons the form (default: every move), in place of
    `allowSearchChanges`; `pathnameChanges` is the old behaviour. The record
    form passes one: a move leaves it when the pathname changes, or any search
    key but the record's tab (`RECORD_TAB_SEARCH_KEY`) does, so a tab switch on
    a form with unsaved edits no longer asks to leave.

  Upgrade:

  - `allowSearchChanges: true` becomes `leaves: pathnameChanges`.
  - A keyed write takes its key from `useIdempotencyKeys().send(scope, fire)`
    under an `AppRuntimeProvider` (`createUiTestProviders` provides one); a key
    minted per attempt (`crypto.randomUUID`, `createClientKey` at send time) or
    held in a ref moves onto it.
  - **Changed contract:** every surface the unsaved-changes guard protects
    renders under the app's router and `ModalsHost`, as every app page provides
    both: a `DialogForm` given `dirty`, from the moment it opens, so every open
    `MutationDialog` and typed-args `ActionFormDialog`; the record form
    `FormView`, editable or not, with the create and edit dialogs a relation
    field opens; and a page guarding its own edits (the dashboard surface, the
    appearance settings page, the workflow studio). Rendered without the router
    such a surface throws at render
    `useUnsavedChangesNavigationGuard must be used under the app's router.`, and
    without `ModalsHost`, `useConfirm must be used under ModalsHost.`. A test or a
    story provides both through the shared providers: `createUiTestProviders`
    (`@angee/ui/testing`) and `ShellPageTestProviders` (`@angee/app/testing`)
    render the same app shell, `AppShellTestProviders` (`@angee/ui/testing/shell`),
    which always provides `ModalsHost`. `ShellPageTestProviders` renders under a
    memory router by default, as every shell page renders under the app's router
    (`{ initialEntries }` places it; `onRouter` receives it), and under a router a
    test or story brings around it, it renders that one alone; entries asked for
    inside such a router are refused. A story renders under the workshop's routed
    fixtures (its decorator, `RoutedRuntimeFixture`, `ComposedRuntimeFixture`).
    `createUiTestProviders` renders under one when given `router: true` (or
    `{ initialEntries }`, kept in `routers`): a component test that opens a real
    dialog passes `router`.
  - A new entry point, `@angee/ui/testing/shell`, exports `AppShellTestProviders`
    and `AppShellTestProvidersProps`. It imports no test runner, so a story
    bundles it as a test does, and `@angee/app/testing` takes the shell from it,
    so that entry reaches no test runner either: a story may import its
    `ShellPageTestProviders` and `ChatterTabsTestHost`. `@angee/ui/testing` keeps
    `createUiTestProviders`, whose spied data providers need Vitest, and a story
    never imports it.
  - `createMutationDialogTestDouble` sends a keyed dialog's submission its
    draft's key through the dialog's own key owner, one per double: kept through
    refusals and lost answers until an accepted answer or a spent key; it minted
    one per submit.
  - A keyed `MutationDialog` submission reads its third argument as a `string`;
    one that asserted it drops the assertion, and one that called `restart`
    after a conflict drops the call: a conflict, thrown or returned with its
    `code`, ends the draft at the owner. Two surfaces holding one
    draft scope at once fail in development: give one its own scope, or nest it
    under its host with `useDraftScope`.
  - A test that dismissed a dialog mid-submission finds it open; one that
    dismissed it with typed values finds the unsaved-changes question; one that
    read Cancel's `disabled` while it sends finds `aria-disabled="true"`. A
    `DialogForm` footer function takes `{ dismiss, dismissRefusal }`.
  - Code that told an envelope refusal from other errors by `instanceof Error`
    alone still works; one that read `response.errors` on a gateway failure finds
    it absent.

### Patch Changes

- 37387b7: Under an `AppearanceProvider`, the user menu's light and dark toggle reads and
  sets only the provider's colour scheme: it no longer mounts the browser-local
  `useColorSchemePreference`, which re-applied its own scheme (`system` unless
  set in that browser) to the document over the saved preference when the shell
  mounted, when the system scheme changed and on a storage event. A tree without
  a provider still uses the browser-local scheme.
- 2be0fbd: A record form's create no longer displays a field its save cannot write that
  holds an empty object, as it already did not display one holding no value, a
  text of blanks or an empty list. What a record form displays now reads its own
  fact of holding nothing to display (`holdsNothingToDisplay`: `null` or
  `undefined`, a text of blanks only, an empty list, an empty object), where it
  read the rule check's, for which an empty object is a value. So a read-only
  JSON field a create seeds with `{}`, as a feed's configuration or an
  integration's sync progress and last sync summary are, is no longer shown as an
  empty object, and a group left with nothing to show is left out. An edit
  displays every field, as before. What the form checks is unchanged (a required
  JSON field holding `{}` is not missing), and so is what a create sends.
- 2d1043b: A resource field's metadata may carry `createOnly`: the field is a create-only
  implementation selector, which its update input carries and its model's save
  lets change only while the stored key is not registered. A record form reads it
  as the field's `createOnly` (`fieldsWithMetadataDefaults`), so a page no longer
  restates it, and a page's own `createOnly: false` cannot unlock it. `fieldChangeable`
  says whether a person may change a field on a stored record (updatable and not
  create-only); the board's lane drag asks it. A resource's roots may name
  `repairChoices`, its generated repair read `<res>_repair_choices(id, field)`.
  `@angee/refine`'s operation documents gain the `repairChoices` kind
  (`RepairChoicesDocumentMap`), which `angee-web-codegen` derives per resource
  whose roots name the read, as `repairChoicesDocuments` in each schema's
  `actions.ts`, validated against that schema; a root the schema lacks fails
  codegen by its name.
- ffdd6a6: `useAngeeFacets` also returns `errors`: each active facet's own read or decode
  error, or `null`, by facet id; `error` stays the first of them. A hand-built or
  mocked result must include `errors`. A facet's read opens no notice; the search
  panel and the facet shortcut show a refused facet's words above the options its
  last read still holds, and the shortcut's list is described by them.
- 1f3a8b4: A failed request of a verb bound with `useRecordActionMutation`, or settled
  through `useActionResultMutation` or `useRecordChromeActionMutation`, is told
  once. These hooks already toast a failed request themselves (the action bar
  under the verb's label; the settle owner under the error's message), and the
  data layer raised its own "Something went wrong" beside it: two toasts for one
  failure. They now turn the data layer's own error notice off for their verb, as
  the stack's rule for a surface that owns its error feedback asks; sign-in
  errors are still handled by the data layer.

  Not changed: `useActionOutcomeMutation`, which leaves the outcome to its
  caller, still raises the data layer's notice unless its caller turns it off. A
  caller that shows the failure itself (its own settle call, or a dialog's
  `submit`, which is handed the option and must pass it on) and does not pass
  `errorNotification: false` still tells a failed request twice; several do
  today.

- 30b173f: The keyboard focus ring is drawn. `focus-ring` and `focus-ring-danger` were
  plain classes inside `@layer utilities`, and Tailwind gives a variant to a
  registered utility only, so every `focus-visible:focus-ring`,
  `focus:focus-ring`, `focus-within:focus-ring` and
  `data-[popup-open]:focus-ring` compiled to nothing: links, buttons, inputs and
  the other controls that ask for a ring showed no focus indicator at all. Both
  are now declared with `@utility`, as `selected` is, and a test compiles the
  stylesheet and holds a rule for each variant the packages spell.

  This is a visible change wherever the stylesheet is used: a 3px ring
  (`--ring`, or `--ring-danger` on an invalid control) now appears on every
  control that asked for one, on keyboard focus, and where a control spells it
  so, on any focus, around a control whose inner field has focus, or while its
  popup is open.

  Upgrade: look at your own screens with the keyboard. A container of yours
  that clips (`overflow-hidden` or `overflow-auto`) cuts a ring on the sides
  where the control touches its edge, and a theme that sets `--ring` is seen for
  the first time. A class of your own that you compose through a variant needs
  `@utility` too.

- 3ad391f: A form dialog opens with focus on its first field. Before, every dialog built on
  `DialogForm`, every `MutationDialog` and `ActionFormDialog` among them, opened
  with focus on its Close button, so a person who opened one and typed at once
  wrote nothing until they moved focus into a field. A field is a control that
  holds a value, such as a text input, a select, a switch or a record picker; a
  read-only, disabled or hidden one is passed over, and a control that only
  performs an action is never one. With no field, focus stays on Close. Opened by
  touch through its trigger, the dialog focuses itself, so no on-screen keyboard
  rises over it. A dialog opened through its `open` prop cannot tell a touch, so
  where the primary pointer is coarse every opening focuses the dialog itself, one
  from the keyboard through its trigger included: a tablet with a hardware
  keyboard never gets the field at the opening, while on a touch laptop, whose
  primary pointer is fine, a dialog gets its field unless a touch on its trigger
  opened it. A field whose widget's code is still loading when the dialog opens is
  passed over, and so is a date or date-and-time field, a status bar and an empty
  list, whose controls are plain buttons. With focus in a text field, Enter
  submits the form when its submit is enabled, as it always did once a person had
  clicked into the field; before, Enter at the opening pressed Close. This shows
  in a dialog whose submit is enabled as it opens, such as one that asks only for
  an optional reference before marking something done: a fresh Enter there now
  confirms it. A key that is already down when the dialog opens does nothing in
  its form, so the press that opened the dialog, held or released, neither submits
  it nor changes its first field; a fresh press acts. Where focus is on the dialog
  itself, keys do not pass through its form: a held Enter or Space does nothing
  there, but a held Escape closes the dialog. Where focus goes when the dialog
  closes is unchanged, and no caller passes anything new.
- bc9d550: A form section that holds only hidden fields shows nothing. A `hidden` field
  keeps its value in the form and in what is saved, and renders no control. A
  group whose fields were all hidden still counted as having something to show:
  the sheet drew its heading over an empty grid, and a pane group listed a pane
  with nothing in it. Such a section is now empty, as one with no field is: no
  heading, no pane. Its values are saved as before.
- 075e9bb: A row whose implementation key the registry no longer names reads that column as
  null, and the console says so instead of showing an empty value: a list cell of
  an implementation-key column (one the resource's `implFields` names) reads "Not
  registered" (`implementation.notRegistered`) through the status badge, in the
  warning tone. On the record's own form the field carries "Not registered" as an
  attention mark, from the stored record (a create form never shows it) and while
  the form holds no other choice (picking a registered one clears the mark until
  the draft is discarded). The mark explains itself, as its field's accessible
  description (and its own, where the mark is the keyboard's stop) and on hover:
  where the field offers every registered implementation, "Its implementation
  cannot run until another is chosen."; where it is read-only on that form
  (declared `readOnly`, a column the update does not write, or a person who
  cannot write the record), "Its implementation cannot run. It cannot be changed
  here." A create-only selector holding such a key is offered once more (see
  `create-only-repair.md`). The record's other fields save either way; a
  refused choice shows the server's reason under the field. A dialog's field of
  the same name carries none. A list cell reads the stored row too, so a column
  the row redacts is not marked where the row carries its `redacted_fields`; no
  list read selects them, so a list still marks a selector column it redacts
  (latent: the record form is the one read that selects them). A null in any other column, or in a column reached
  through a relation (`channel.backend_class`), renders as before.

  A list grouped by such a column, a board laned by it and a facet counted on it
  still cannot be read for such a row: the server refuses, publicly and in words
  naming the column, and the list and the board show those words once, without
  retrying. A refused facet says why above the options its last read still holds;
  each facet was already its own read, so the other facets kept counting before
  too. There is no "Not registered" group, lane or facet bucket. A filter chip
  offers only registered implementations.

- d42d134: A member of an object field (a FormSpec `object` widget) named `type` shows its
  message. Before, a value that broke such a member's rule showed nothing on
  screen: a `MutationDialog` disabled Save, and a record form and an
  `ActionFormDialog` built with `jsonSchemaActionArgs` refused the submit, with no
  message and the member not marked invalid. Now its message shows under the
  member as any member's does, stays through an edit that still fails, and goes
  when the value is corrected. A member named `ref` still shows no message. When
  the server refuses the object field and its member named `type` together, the
  two errors meet at one key: if the member's refusal is applied first, its
  message is lost and only the field's shows; if the field's is applied first,
  both show, but `serverErrorsFromForm` returns only the member's. Which rules
  fail and what a form saves or sends are unchanged.
- 2881e0d: A form no longer refuses its own seed. An optional field with a bound (or
  `omittable`, or a template) whose empty value is `null`, and whose declared
  type does not refuse that `null`, was seeded with it and then refused by the
  rule check as "This field is required.", because a held `null` the field does
  not allow counted against it for holding the key. That is a field declared by a
  number widget, or a date or time widget, alone, or one of kind `any`: a record
  form's create and its edit of a record that lacks the field were refused under
  an optional field the person had not touched; a `MutationDialog` was refused
  the same way, or showed its submit disabled with no message beside a required
  sibling. The same held one level down, for a member of an object field whose
  empty value is `null` and whose type does not refuse it, bound or not.

  The rule that judges a held value (`structuredFieldErrors`, at the field's own
  path and at each member's) now composes the seed rule: the `null` a form gives
  a field that holds nothing (its `emptyValueForField`, which `refusesSeed` does
  not refuse) is not held against the field for holding the key. Not changed: a
  required field and a presence-required one answer for a `null` as before
  (refused, unless the field may be null, where `null` is a value); a value
  outside its bound is refused; a field whose own declared type refuses `null` (a
  typed FormSpec number that is not nullable) is not seeded with it and is still
  refused when it holds one; a `null` that is not the field's own empty value
  (a text field holding `null`) is still judged; and a list item that holds
  nothing is still refused, whatever seeded it, since an item has no key to
  leave out.

  At a field's own path no declared form has the shape: no descriptor bounds a
  field without a kind. A member needs no bound to have it: an untyped member of
  an object field, which the server's form projection emits for a JSON field,
  was refused when it held `null`.

  Known limit: a record form's edit of a record that lacks such a field as a
  member of a JSON value sends the member as `null`, as it already does for the
  same field without a bound. A server member typed as a plain number with a
  default accepts the key left out and refuses `null`; a field over such a member
  declares its kind, and is then not seeded with `null` on a create or in a
  dialog.

- 804e3d3: A list read's order reaches the server as an ordered list, so a list ordered by two keys or
  more reads in the priority it shows. A GraphQL server rebuilds an input object in its type's own
  field order, so `order_by: { due: "desc", title: "asc" }` sorted by whichever of the two the
  resource declares first; and the read's key, the list batch hash and a grouped list's scopes
  compare an object with its keys sorted, so `due desc, title asc` and `title asc, due desc` were
  one request, and a pure swap of priority (a saved view, a link, back and forward) read nothing
  again.

  `ResourceQuery.toOrderBy` returns one single-key object per key, in priority order
  (`[{ due: "desc" }, { title: "asc" }]`), and an empty list for no order. `@angee/refine` names
  that shape `ListOrderBy` and takes it where a list's order goes: `listQueryMeta`'s `orderBy`,
  `GroupRowsRequest.orderBy` (`groupByVariables`' `rows_order_by`) and
  `AngeeListBatchScope.orderBy`, which no longer accept an object. A list read's `sorters` go the
  same way, one object per sorter in the order given, a dotted field nested, through
  `@refinedev/hasura` 7.0.1 as Angee's pnpm install policy patches it: the stock `generateSorting`
  merges them into one object. In `@angee/ui` the record pager's captured scope, the navigation
  scope a list publishes and a gantt's bar order follow a pure priority swap.

  **Upgrade:** pass `toOrderBy`'s result on as it is wherever a list's `order_by` goes. A caller
  that wrapped it in a list (`[query.toOrderBy(order)]`) unwraps it, since the server refuses a
  list inside a list; code that read its result as an object reads the list. A project that
  installs through Angee's pnpm policy (`packages/scripts/pnpm-policy.cjs`) gets the
  `@refinedev/hasura` patch; one that does not keeps the stock merge, so a read with several
  `sorters` is ordered by the server's field order, not the sorters' priority.

- 37387b7: The `password` widget is the masked entry of a secret that is not the person's
  own sign-in password, and tells the browser so with `autocomplete="new-password"`
  when the field declares no `autoComplete` of its own, so a vendor key after a
  name field is no longer filled with the saved sign-in. Its read form shows
  nothing for an empty value instead of a mask. The `textarea` widget shows the
  field's `placeholder`, and its label only when the field declares none.
- c55f0b8: A press on a grouped list's column header never drops the keys a group shares that the
  list's order holds. Before, on a list grouped by status and ordered by status, rank, then
  title, a press on Title (a tie-breaker), on Rank (the shown key, when it is not the
  order's last) or on a column the order does not hold set that column alone: the status
  key went, so a list grouped in the browser reordered its groups by the pressed column's
  value on each group. Now each of those presses keeps the status key, behind the pressed
  column, `title:asc,status:asc` or `rank:desc,status:asc`, so the pressed column shows its
  direction and the URL and a reload keep the order; the groups keep their order, in the
  browser and on the server, since they follow their own axis whatever a press sets (the
  note on groups following their axis). A list grouped in the browser by a field its order
  does not lead with, such as a grouping chosen in the toolbar, keeps its groups too.
  For the pressed column itself a press is the table's own single-sort toggle, as before, its
  key placed ahead of the group's keys, and a tie-breaker still sorts in its first direction.
  A press on a grouping column's own
  header turns its groups, ascending and descending in turn, never removing its key, and
  keeps the keys the rows follow and, with several grouping levels, the other levels' keys (the note
  on groups following their axis). A list that groups nothing presses as before.
- 37387b7: `useAuthoredQueryBatch` keeps its Map and each entry's identity until a read its
  callers use changes, so a grouped list no longer rebuilds on every render. Its
  entries are now `QueryBatchEntry` (exported): a readonly `data`,
  `dataUpdatedAt`, `error`, `isFetching` and `refetch`, no longer the whole
  `UseQueryResult`. The new `useDerivedBatch(batch, derive, inputs)` maps a batch
  and reuses each output while its entry is the same object and `inputs` are
  unchanged. A group payload the decoder refuses is now that read's `error` in
  `useAngeeGroupByBatch` and `useAngeeFacets`, as it already was in
  `useAngeeGroupBy`, instead of throwing in render, and in all three it is
  reported once per settle as a failed read is (refine's auth check and error
  notification). Upgrade: read any other query property (`status`, `isStale`)
  from your own `useQuery`.
- 37387b7: A record form fits the space it is given. Its tabs that do not fit move into a
  "More" menu, keeping the selected tab visible, and two-column sections follow
  the width of the sheet or drawer rather than the viewport (`FormGrid`'s
  `adaptiveTwo` columns, which also never exceed two). A field placed in a tab
  explicitly stays in that tab instead of being pulled into the form's header. In
  a create form, a read-only title that is still empty reads after the model
  ("New task"), a routed create form's breadcrumb reads "New", and a read-only
  status that is still empty is not shown.
- c163be9: A record form mounts a pane the same way whether it has one pane or several, so
  a pane that stays selected, or is kept mounted (`keepMounted`), is no longer
  rebuilt when another pane comes or goes. Before, when the number of panes
  crossed between one and several while the form was open (a value giving a pane
  its first field or taking its last, a record tab's `visibleWhen` after a save
  or a fresh read of the record), the remaining pane's whole content was mounted
  again: a field being typed in there lost its node and its focus, and a
  component in it, such as a tab kept mounted, lost its own state. A lone pane is
  still the plain region beneath the sheet under its heading, with the same
  markup and no tab role, and several panes are still tab panels labelled by
  their tabs.

  Which pane is active is unchanged. A pane that appears ahead of a lone pane the
  person had not chosen takes the selection, and a pane the person had chosen
  that is dropped gives way to the first pane and is selected again when it
  returns; a pane that loses the selection and is not kept mounted is unmounted,
  as any inactive pane is. A create that is saved still mounts its form again,
  as before.

- 2c3e5b7: A record form that holds a draft keeps what a refused save said about it across
  a re-read of its record: the server's refusal stays under its field until the
  person changes that field or discards. A re-read that replaces a value the
  person has not changed takes that value's error away, a member path's
  (`config.root`) included, and a re-read that leaves no draft, because another
  writer stored the person's own values, takes the draft's errors with it. A form
  holding no draft is reset whole, as before. Every record form behaves so, not
  only one repairing an implementation selector.
- 802559c: A refused save on a record form brings the person to its first refused field
  even where the form does not show it. A field in a pane group that was not the
  selected pane (another pane group, a saved-record tab, kept mounted or not, or
  the trailing lines, on a saved record or a create form) had no mounted control,
  or only the one of a pane opened earlier, so the refusal's focus reached
  nothing and the banner took it. Now the form's surface reveals the field first,
  in the same batch: `revealField` selects the pane that holds it and names it to
  the sheet's sections, so a collapsed section holding it opens, and the
  refusal's focus then lands on its control. A record verb whose patch the form's
  own `submit` refuses (`applyPatch`, behind a `set` verb or a `run` verb's
  `update`) reveals its refused field the same way. `focusField` composes the
  same `revealField`, so which pane holds a field has one owner. No caller passes
  anything new.

  The form's own check does the same: a required field left empty in a pane that
  is not selected, where Save focused nothing, selected no pane and showed
  nothing, now has its pane selected and takes focus.

  With a reason no field shows as well, the banner takes focus and the selected
  pane stays.

  `revealField` is the only thing that selects a pane for a refused save. The
  trailing lines selected their own pane after any failed save that touched them,
  beside it; they no longer do. So a line refused beside a reason no field shows,
  and a save refused because the lines changed elsewhere, leave the selected pane
  and say it on the banner; a pane group's field refused together with a line
  ends on that field, in its pane, the form's fields coming before its lines; and
  a pane the person picks after a refusal stays selected: when the pane strip goes
  and returns, and on a record whose caller holds its pane (a resource list's
  record panel), where every pane picked after a refused line was sent back to the
  lines. A form whose only pane is the lines, shown without a strip, writes no
  pane for its caller on a refusal. The form's own check failing under a line
  selects the lines, as before;
  failing in a pane group's field and under a line at once it selects that field's
  pane, while the form library's own focus still aims at the line's cell, which
  that pane hides.

- 2bea088: A record action the server refuses tells the person why. When a verb's target is
  refused before the verb runs (not found, not allowed), the server answers with the
  verb's summary as the message ("Confirm failed.") and the reason in-band, in
  `validation_errors`. `runActionResult`, which projects an action's outcome onto
  the rendered action's contract, threw only the message, so the action bar's toast
  read "Confirm / Confirm failed." and the reason was lost. It now throws the
  reasons, and the message when the refusal carries none. Every record action
  projected through `runActionResult` now shows the in-band reason without an
  edit. No toast title changes. Other refusals (a transition not allowed, a
  missing object) still read the verb's summary: the server sends no reason for
  them.

  `@angee/refine` exports `actionOutcomeReasons(outcome)`: a refusal's in-band
  reasons, under any key, each once, as one line. `useActionResultRun` composes it
  instead of its own copy of the rule, and so does a refused row of a bulk trash
  or restore, which showed only the first reason.

  Upgrade: code that matched the thrown error's text against the outcome's
  `message` finds the reasons there when the refusal carries any.

- 499bc0e: A refused owner cell or progress bar now takes the focus a refusal brings it,
  and a refused composite field or absent value keeps it.

  - The `ownerCell` and `progressBar` widgets hand the form their control
    (`controlRef`): the owner cell its select's trigger, the progress bar its
    number input. A refusal of either focused nothing, and the banner took it.
  - `useRefusalFocus` counts focus anywhere inside a field shown invalid as focus
    in a refused field, not only on the element marked `aria-invalid`. An
    `object`, `list` or `rows` field refused as a whole focuses its first member,
    and an absent value its "Set value" button; neither is marked invalid itself,
    so the banner took the focus from them.

  Not changed: a refused member of an `object`, `list` or `rows` field (a reason
  under `lines.0.qty`) still focuses no control, and a record form's `rows` field
  refused as a whole brings focus to the banner, not to its first cell.

- e3bf740: The Links pane says it is empty when it is. A related group with neither rows
  nor a verb is left out of the pane, and the pane showed its empty state only
  when no group was eligible at all. A record whose eligible groups were all left
  out (each counted nothing and offered no verb) showed "Links 0" over a blank
  panel. The pane now shows the same empty state then.

  Each group tells the pane whether it has rows or a verb, since only the group
  knows its row count and its verb. A group that is still counting is taken to
  show something, so the empty state does not flash while counts load, and the
  list that holds no row is hidden, and so out of the accessibility tree, while
  the empty state stands for it.

- 4032bc5: A record form confirms a save as soon as the write answers. After a successful
  write the save awaited the refresh of every dependent resource (`invalidateModels`)
  and of every authored read registered on the saved model, among them the reads
  the record's own chrome mounts on every saved form (provenance marks, the
  timeline's feed, the follow thread, the readers roster). One slow read delayed
  "Changes saved" by as long as it took; one that failed was retried first,
  about seven seconds under the app's query defaults; one that never answered
  held the confirmation for ever, and until it settled Save sent no further
  write, so the form could not be saved again. The form itself had already
  turned clean, so the save looked hung.

  The save now starts those refreshes and does not wait for them. What the form
  shows is its own record, which the write's answer carries. A read that fails
  reports through its own query, as before.

  What changes for a reader: a list of a dependent model, or an authored read of
  the saved model, may show the saved state a moment after the confirmation
  where it showed it just before. The saved model's own lists already refreshed
  after it. A save that writes lines still waits for its own model's lists. A
  record action and a dialog still wait for their refreshes (`useAuthoredMutation`).

- 8b9f4fe: A record form's save no longer writes into the values it reads. Where a form
  saves a structured field (an object or JSON field) together with one of its
  dotted members, and the member goes on the wire as another value than the form
  holds (a cleared number as `null`, a picked record as its id), the payload
  builder (`mutationData`) put the structured field's own object into what it
  sends and then wrote the member's wire value through it, into the values the
  save had read. It now copies what it takes from them (`structuredClone`, as it
  already copied a hidden reset). For a form that saves such a pair, two things
  change. After a successful save the form is no longer left marked as changed,
  with Save and Discard still showing and the cleared or picked value held
  against a baseline that recorded its wire value: it is clean and takes the
  saved record's values. And a form's own submit owner receives in
  `context.values` what the form held, where it received the member's wire
  value. No declared form of the framework or of its products saves such a pair
  today.

  What a form sends is unchanged for a record form's values, which are plain
  data: the form's undo history already copies them the same way on every
  change. A value of another kind would change: a class instance would be sent as
  a plain copy without its methods (a `toJSON` among them), and a value holding a
  function would throw, as the undo history already throws when it is set. No
  widget or form of the framework or of its products puts such a value in a
  record form.

- 37387b7: An `ANGEE_UI.shell.apps` entry may omit `rail`: such an app shows every menu
  root with its own brand and theme, else the shell's, so `apps: {console: {}}`
  makes `?app=console` the full console on any host (such an entry may not set
  `home`; the boot check refuses one). The new `ANGEE_UI.shell.app` names the app
  a page opens when neither `?app=` nor the hostname's `hosts` entry selects one,
  and is checked at boot like `hosts`. A `RuntimeSelection` (`@angee/ui/runtime`)
  may therefore name an `app` while its `rail` is `null`, which the developer
  summary shows as "(every root)".
- d2a5fe7: `SplitPane`'s recipe (`splitPanesVariants`, its `pane` slot) no longer carries
  `overflow-hidden`, a class that never applied: react-resizable-panels sets
  `overflow: auto` inline on the element that takes the pane's class, and an inline
  style wins over any class that is not `!important`. A pane scrolls its content as it
  did, and nothing computed changes; the pane's docstring now says so. A record in the
  document presentation that a list opens in its split placement scrolls in the
  content pane of the list's own workbench, as one opened inline scrolls in the list's
  record sheet; a record in the workspace presentation, or with a full-bleed pane
  active, scrolls inside the form in either placement. `Workbench`'s `scrollMode`
  docstring now says that the prop does nothing (both values compute the same layout),
  and the console layout no longer passes its default.
- 58eabd4: A record form's `object`, `list` or `rows` field no longer shows its loading
  placeholder for good in a create form, in an edit form once the widget's code was
  loaded, or for a record that lacks the field: the form gives an `object` field an
  empty object and a `list` or `rows` field an empty list, as it gives a `json`
  field an empty object. An untouched `object`, `list` or `rows` field is therefore
  sent as an empty container (`{}` or `[]`) when a record is created, as untouched
  `json`, `tagInput` and `many2many` fields already are; before, it was left out,
  so the server's default or NULL applied.
- 6722b98: The toast region is named in the app's language. `ToastProvider` passed no
  label to the toast library's viewport, so the region a screen reader announces
  and a person reaches by landmark was always the library's English default,
  "Notifications". It now reads the `ui` namespace's new `toast.region` key
  (English: "Notifications"), which a host's translations answer like every other
  key.
- 946895d: `createUiTestProviders` opens a hovered tooltip in the same event
  (`TooltipProvider delay={0}`) instead of after Base UI's 600 ms open delay, so a
  hover assertion no longer races the clock. A nested `TooltipProvider` keeps its
  own delay.
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [2d1043b]
- Updated dependencies [ffdd6a6]
- Updated dependencies [54cdc10]
- Updated dependencies [39bec0a]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [804e3d3]
- Updated dependencies [37387b7]
- Updated dependencies [e7839e0]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [2bea088]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [37387b7]
- Updated dependencies [e3cb0a0]
- Updated dependencies [37387b7]
- Updated dependencies [616e025]
- Updated dependencies [7214202]
  - @angee/refine@0.2.0
  - @angee/metadata@0.2.0
