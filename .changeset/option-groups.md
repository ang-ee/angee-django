---
"@angee/ui": minor
---

Pickers list options under headings by one rule, `optionGroups` (exported from
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
