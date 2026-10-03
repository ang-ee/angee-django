// happy-dom omits the Web Animations enumeration used by native UI primitives.
// Keep this in the published Vitest defaults so package and addon tests agree.
if (typeof Element !== "undefined") {
  Element.prototype.getAnimations ??= () => [];
}
