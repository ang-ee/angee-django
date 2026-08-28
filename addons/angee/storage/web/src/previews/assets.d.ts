// Asset imports the preview renderers reach for: side-effect stylesheets shipped
// by react-pdf / vidstack, and Vite's `?url` form for the pdf.js worker. The
// bundler resolves both; these declarations only satisfy `tsc` (the package sets
// `types: []`, so it does not pull in `vite/client`).
declare module "*.css";
declare module "*?url" {
  const src: string;
  export default src;
}
