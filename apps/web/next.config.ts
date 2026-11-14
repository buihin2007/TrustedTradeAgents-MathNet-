import type { NextConfig } from "next";

// In docker compose, nginx routes /api to the buyer API. In `npm run dev` without nginx,
// set API_PROXY_URL=http://localhost:8080 so /api/* is forwarded there.
const config: NextConfig = {
  output: "standalone",
  async rewrites() {
    const target = process.env.API_PROXY_URL;
    return target ? [{ source: "/api/:path*", destination: `${target}/api/:path*` }] : [];
  },
};

export default config;
