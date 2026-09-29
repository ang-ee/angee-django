# ReUI Base UI Gantt

MIT, Copyright (c) 2025 Keenthemes Inc; see LICENSE.

Source: https://github.com/keenthemes/reui/tree/6e433ddaba3a4be38182c8c8883b6cc335183c42/registry-reui/bases/base/reui/gantt

The supplied eight files matched this commit byte for byte. The required ninth
file, `gantt-recurrence.tsx`, was retrieved from that same commit.

Local adaptations: relative imports, Angee glyphs, token names, radius and button
recipes, explicit primitive portals/positioners, scroll-area parts, and type annotations for
checked array bounds under `noUncheckedIndexedAccess`. Generic React context
boundaries erase data to `unknown` and restore it in the typed hooks. Recurrence,
baselines, packing and interaction algorithms remain intact; layout adaptations
are listed below.

Original SHA-256:

```text
gantt-bar.tsx 973f22b905c2a1d843a32cefcad4a3d2e02ba12ebed630029dc2efe27bc59ace
gantt-dnd.tsx 0dde42edfaa0eb4591d48aec28a6be660a06d457217db0375623eb8662a6a7e4
gantt-i18n.tsx 1f16cd9c680b5247091236a8f24f882704c66f38a35415d108df6a7b98f356bc
gantt-lib.tsx caa9cd86dc78c0b2282f3df9bf309ccb219c08f587bc8e72eacc6ca7033215d0
gantt-nav.tsx b256e741bdfe3cebc6c3dda2a9446fcc49828ab2ca6c0b5986d2b5f766d1c148
gantt-recurrence.tsx 1f1509c1156071f09f1f68607382f7f094d4282d5591ec9952b0ffd4278a67dc
gantt-types.tsx e1f3420a2d12bca111b392a19649437ef98a8ce82d9119a278e5f0e5643cfd32
gantt-view.tsx 1eb19b2035cab28a711d0a5390282ce587ebaef6ed07ff3297fc8f62bf591e59
gantt.tsx e23fa37a03b7064c883abe3535eddb15a6d755d4eecf1b8bd5a67dfdad60845d
```

Fix round 1 adaptations: unused upstream declarations removed (`PackedPosition`
and `ComponentType`). Controlled event/resource inputs accept readonly arrays,
so callers retain their references. Restored the upstream `ScrollAreaContent`
ResizeObserver rationale. Glyph mapping preserves upstream meaning: `RepeatIcon`
uses the registered `repeat` glyph (Lucide `Repeat`); the collection switcher uses
`chart-gantt` (Lucide `ChartGantt`).

Local (non-upstream) files in this directory: `GanttView.tsx` (public lazy
boundary), `gantt-surface.tsx` (read-only presentation wrapper) and
`gantt-collection-surface.tsx` (resource-view collection adapter).
`gantt-recurrence.tsx` replaces the unary `+y` coercion with `Number(y)`.
`warnOnce` drops upstream's `process.env.NODE_ENV` guard: the composed host
typechecks without Node types, and a once-per-key warning is harmless in production.

Projects timeline adaptations: packed rows and their body cannot flex-shrink
below the shared calculated height. Flat, checkbox-free trees omit the unused
toggle gutter. Zoom controls occupy a reserved row above both scrolling panes;
the former floating-control/offscreen-chip collision shifting is removed.
`GanttRowLayout` exposes sidebar width and minimum row height through the public
view and collection spec, reusing the existing tree-panel and metrics contracts.
The headless state accepts a controlled `range`, used by the read-only surface's
initial week-aligned fit-to-events window; navigation returns to native periods.
The collection adapter uses metadata Date fields as inclusive calendar days,
translated to the existing exclusive-end/all-day event contract.
