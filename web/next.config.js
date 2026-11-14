/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The desk is one screen now. Old links open their tab or sheet; their query (?cell=, ?form=) is kept.
  async redirects() {
    return [
      ["/map", "map"],
      ["/operations", "report"],
      ["/response", "dispatch"],
      ["/sectors", "sectors"],
      // Hidden from the frontend for now; see SHEETS in components/desk.js.
      // ["/impact", "impact"],
      // ["/model", "model"],
      // ["/endpoints", "api"],
    ].map(([source, id]) => ({ source, destination: `/#${id}`, permanent: false }));
  },
};

module.exports = nextConfig;
