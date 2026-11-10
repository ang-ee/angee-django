import { afterEach } from "vitest";

// happy-dom omits the Web Animations enumeration used by native UI primitives.
// Keep this in the published Vitest defaults so package and addon tests agree.
if (typeof Element !== "undefined") {
  Element.prototype.getAnimations ??= () => [];
  // A waiting query (`findBy*`, `waitFor`) may take as long as a CI runner's cold
  // first render: the first DOM test of a file pays the page's imports and first
  // paint, measured there at up to 2.1 s against Testing Library's 1 s default. A
  // wait spends the limit only when it would otherwise fail, so passing tests are
  // no slower, and it stays under Vitest's 5 s test limit so a failed wait still
  // reports its own error. Raise this one value on new evidence; a per-test
  // timeout for a slow render is forbidden. Testing Library is an optional peer:
  // a suite without it has no waiting queries.
  const testingLibrary = await import("@testing-library/react").catch(() => null);
  testingLibrary?.configure({ asyncUtilTimeout: 4_000 });
  // Testing Library unmounts on its own only under Vitest globals, which these
  // defaults leave off, so every DOM test unmounts here. A file's own `afterEach`
  // hooks run first; one that must tear down after the unmount calls `cleanup()`.
  if (testingLibrary) afterEach(testingLibrary.cleanup);
}
