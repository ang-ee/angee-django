/**
 * Tell every link of a form's markup how to open: in a new tab, so a person who
 * follows one does not lose a half-filled form, and without handing the new page
 * a reference to this one.
 *
 * A form's `html` elements and its completed page are its only texts that are
 * markup. The server stores them as simple markup in which a link carries its
 * address and nothing else, so this is the one place a link gets its `target`
 * and `rel`. The markup is parsed into a document of its own and never runs.
 */
export function linksInNewTab(html: string): string {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  for (const link of parsed.querySelectorAll("a")) {
    link.setAttribute("target", "_blank");
    link.setAttribute("rel", "noopener noreferrer");
  }
  return parsed.body.innerHTML;
}
