import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  // Stabilized in Next 16 — moved out of `experimental`.
  typedRoutes: true,
};

export default nextConfig;
