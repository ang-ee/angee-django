import { createContext, useContext, type ReactNode } from "react";

const ActiveDataProviderNameContext = createContext<string | undefined>(undefined);

export function ActiveDataProviderNameProvider({
  children,
  name,
}: {
  children: ReactNode;
  name: string | undefined;
}) {
  return (
    <ActiveDataProviderNameContext.Provider value={name}>
      {children}
    </ActiveDataProviderNameContext.Provider>
  );
}

export function useActiveDataProviderName(): string | undefined {
  return useContext(ActiveDataProviderNameContext);
}
