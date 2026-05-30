"""Order resource entries by their declared ``depends_on`` edges."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from angee.base.resources.entries import ResourceEntry
from angee.base.resources.exceptions import ResourceLoadError

EntryKey = tuple[str, str]


def order_entries(
    entries: Sequence[ResourceEntry],
) -> tuple[ResourceEntry, ...]:
    """Return entries in load order, honoring ``depends_on``.

    Each entry is keyed by ``(addon name, source)``. A ``depends_on`` item
    targets ``<source>`` in the same addon, or ``<addon>:<source>`` to cross
    addons. Unknown or unselected targets and dependency cycles raise
    ``ResourceLoadError``. Entries with no edges keep their original order.
    """

    by_key: dict[EntryKey, ResourceEntry] = {}
    position: dict[EntryKey, int] = {}
    addon_names: dict[str, str] = {}
    for index, entry in enumerate(entries):
        key = _entry_key(entry)
        if key in by_key:
            raise ResourceLoadError(
                f"duplicate resource entry {entry.display}"
            )
        by_key[key] = entry
        position[key] = index
        addon_names[entry.addon.name] = entry.addon.name
        addon_names[entry.addon.label] = entry.addon.name

    outgoing: dict[EntryKey, list[EntryKey]] = defaultdict(list)
    indegree: dict[EntryKey, int] = {key: 0 for key in by_key}
    for key, entry in by_key.items():
        for dependency in entry.depends_on:
            dependency_key = _dependency_key(entry, dependency, addon_names)
            if dependency_key not in by_key:
                raise ResourceLoadError(
                    f"{entry.display}: depends_on target not selected: "
                    f"{dependency}"
                )
            outgoing[dependency_key].append(key)
            indegree[key] += 1

    ready = sorted(
        (key for key, count in indegree.items() if count == 0),
        key=position.__getitem__,
    )
    ordered: list[ResourceEntry] = []
    while ready:
        key = ready.pop(0)
        ordered.append(by_key[key])
        for child in sorted(outgoing[key], key=position.__getitem__):
            indegree[child] -= 1
            if indegree[child] == 0:
                ready.append(child)
        ready.sort(key=position.__getitem__)
    if len(ordered) != len(by_key):
        raise ResourceLoadError("cycle detected in resource depends_on")
    return tuple(ordered)


def _entry_key(entry: ResourceEntry) -> EntryKey:
    """Return the ``(addon, source)`` key for one entry."""

    return (entry.addon.name, entry.source)


def _dependency_key(
    entry: ResourceEntry,
    dependency: str,
    addon_names: dict[str, str],
) -> EntryKey:
    """Return the key a ``depends_on`` item points at.

    ``addon_names`` maps every selected addon's name and label to its full
    name, so a cross-addon ``<addon>:<source>`` reference resolves by either.
    """

    if ":" in dependency:
        addon_ref, source = dependency.split(":", 1)
        return (addon_names.get(addon_ref, addon_ref), source)
    return (entry.addon.name, dependency)
