---
"@angee/ui": minor
---

A record form tells what its Save would write before a save. Its save surface,
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
  const unsaved = form.formIsDirty ? JSON.stringify(form.nextSave().data) : null;
  const [settled] = useDebounce(unsaved, 400); // from `use-debounce`
  // ...read the preview of `recordId` with `settled && JSON.parse(settled)`
}
```
