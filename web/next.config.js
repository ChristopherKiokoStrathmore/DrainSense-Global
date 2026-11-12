/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // The desk is one page now. Old links land on their section; their query (?cell=, ?form=) is kept.
  async redirects() {
    return [
      ["/map", "map"],
      ["/operations", "record"],
      ["/response", "dispatch"],
      ["/sectors", "sectors"],
      ["/impact", "impact"],
      ["/model", "model"],
      ["/endpoints", "api"],
    ].map(([source, id]) => ({ source, destination: `/#${id}`, permanent: false }));
  },
};

module.exports = nextConfig;
