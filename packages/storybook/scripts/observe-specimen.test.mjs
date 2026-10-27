import assert from "node:assert/strict";
import { test } from "node:test";
import { observeSpecimen } from "../src/stories/design-system/observe-specimen.ts";

test("one observer reports entry, departure and reentry until each specimen unmounts", (t) => {
  const original = globalThis.IntersectionObserver;
  const instances = [];
  globalThis.IntersectionObserver = class {
    observed = new Set();
    disconnects = 0;
    constructor(callback, options) {
      this.callback = callback;
      this.options = options;
      instances.push(this);
    }
    observe(element) { this.observed.add(element); }
    unobserve(element) { this.observed.delete(element); }
    disconnect() { this.disconnects += 1; }
  };
  t.after(() => { globalThis.IntersectionObserver = original; });

  const first = {};
  const second = {};
  const firstVisibility = [];
  const secondVisibility = [];
  const removeFirst = observeSpecimen(first, (visible) => firstVisibility.push(visible));
  const removeSecond = observeSpecimen(second, (visible) => secondVisibility.push(visible));
  t.after(removeFirst);
  t.after(removeSecond);
  assert.equal(instances.length, 1);
  const [observer] = instances;
  assert.equal(observer.options.rootMargin, "200px");
  for (const isIntersecting of [true, false, true]) {
    observer.callback([{ target: first, isIntersecting }]);
  }
  observer.callback([{ target: second, isIntersecting: true }]);
  assert.deepEqual(firstVisibility, [true, false, true]);
  assert.deepEqual(secondVisibility, [true]);

  removeFirst();
  assert(!observer.observed.has(first));
  assert.equal(observer.disconnects, 0);
  observer.callback([{ target: first, isIntersecting: false }, { target: second, isIntersecting: false }]);
  assert.deepEqual(firstVisibility, [true, false, true]);
  assert.deepEqual(secondVisibility, [true, false]);
  removeSecond();
  assert.equal(observer.disconnects, 1);

  const removeRevisited = observeSpecimen(first, () => {});
  t.after(removeRevisited);
  assert.equal(instances.length, 2);
});
