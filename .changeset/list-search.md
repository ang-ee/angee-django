---
"@angee/ui": major
---

Every list shows one combined search box, `SearchBox`, in place of the filter
picker and the Group by control: a chip per active filter, facet, clause, text
term, favorite and group level, typed suggestions, and a panel with Filters,
Group by (`GroupStackPanel`) and Favorites. One headless model
(`useResourceSearch`, `useSearchCatalog`) drives it; `search={{ box, shortcuts }}`
(`ListSearchDeclaration`) adds `SearchControls` shortcuts, contributed through
the `resource#search` container. `defaultGroup` and `defaultGroups` accept a
stack per view kind and are part of the default state. An invalid named filter
throws in development and is dropped in production. Removed:
`useResourceToolbarProps`, `useResourceViewToolbarInputs`,
`useListViewToolbarInputs`, `useResourceViewGroupState`, `defaultGroupForView`,
`defaultGroupsForToolbar`, `validResourceViewGroupStack`, `activeFilterIdsFor`,
`nextFacetFilter`, `nextTextFilter` and `textFilterValue`.
