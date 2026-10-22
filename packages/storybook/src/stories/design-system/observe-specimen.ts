const callbacks = new Map<Element, () => void>();
let observer: IntersectionObserver | undefined;

function disconnectWhenIdle() {
  if (callbacks.size) return;
  observer?.disconnect();
  observer = undefined;
}

/** One observer; a mounted specimen stays mounted after its first intersection. */
export function observeSpecimen(element: Element, onVisible: () => void): () => void {
  observer ??= new IntersectionObserver(entries => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const callback = callbacks.get(entry.target);
      callbacks.delete(entry.target);
      observer?.unobserve(entry.target);
      callback?.();
    }
    disconnectWhenIdle();
  }, { rootMargin: "200px" });
  callbacks.set(element, onVisible);
  observer.observe(element);
  return () => {
    callbacks.delete(element);
    observer?.unobserve(element);
    disconnectWhenIdle();
  };
}
