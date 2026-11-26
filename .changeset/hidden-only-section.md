---
"@angee/ui": patch
---

A form section that holds only hidden fields shows nothing. A `hidden` field
keeps its value in the form and in what is saved, and renders no control. A
group whose fields were all hidden still counted as having something to show:
the sheet drew its heading over an empty grid, and a pane group listed a pane
with nothing in it. Such a section is now empty, as one with no field is: no
heading, no pane. Its values are saved as before.
