---
"@angee/ui": minor
---

A field's `resolve` no longer returns `name`: the field keeps its declared name
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
