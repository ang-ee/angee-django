---
"@angee/app": minor
"@angee/metadata": patch
---

Vite owns frontend generation: `@angee/app/web-codegen` exports
`generateWebRuntime` (with `WebCodegenOptions`, `WebCodegenInputs` and
`WebCodegenResult`) and `@angee/app/shell-runtime` exports
`assertShellSettings` and `assertBrand`. The dev server scans linked package
entries so lazy dependencies are found at startup, and the tooling reads addons
under `src` roots. `@angee/metadata` reuses TypeScript build state for its
preparation and keeps that state out of the published archive.
