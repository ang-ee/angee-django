/** DataTransfer stand-in shared by DnD unit and rendered-list tests; happy-dom's setData is incomplete. */
export function testDndTransfer(): DataTransfer {
  const store = new Map<string, string>();
  return {
    setData: (type: string, data: string) => store.set(type.toLowerCase(), data),
    getData: (type: string) => store.get(type.toLowerCase()) ?? "",
    get types() { return [...store.keys()]; },
    effectAllowed: "none",
    dropEffect: "none",
  } as unknown as DataTransfer;
}
