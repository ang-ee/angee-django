// The aurora theme exports its stylesheet under a bare package subpath. Vite
// loads it as CSS; this tells the type checker the side-effect import resolves,
// since vite/client only declares specifiers that end in `.css`.
declare module "@angee/theme-aurora/styles";
