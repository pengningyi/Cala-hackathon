import type { NextConfig } from "next";

const config: NextConfig = {
  // Onboarding lookups use Cala's knowledge query (~25 s uncached); the default 30 s proxy limit is too tight.
  experimental: { proxyTimeout: 60_000 },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${process.env.BACKEND_API_URL || "http://127.0.0.1:8000"}/api/:path*` }];
  },
};
export default config;
