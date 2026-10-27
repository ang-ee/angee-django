const callbacks = new Map<Element, (visible: boolean) => void>();
let observer: IntersectionObserver | undefined;

function disconnectWhenIdle() {
  if (callbacks.size) return;
  observer?.disconnect();
  observer = undefined;
}

/** One observer bounds live specimens to the viewport and its preload margin. */
export function observeSpecimen(element: Element, onVisible: (visible: boolean) => void): () => void {
  observer ??= new IntersectionObserver(entries => {
    for (const entry of entries) {
      callbacks.get(entry.target)?.(entry.isIntersecting);
    }
  }, { rootMargin: "200px" });
  callbacks.set(element, onVisible);
  observer.observe(element);
  return () => {
    callbacks.delete(element);
    observer?.unobserve(element);
    disconnectWhenIdle();
  };
}
