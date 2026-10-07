import type { NextConfig } from "next";

// The browser calls the Render API directly (NEXT_PUBLIC_API_URL). No rewrites/proxy: SSE must not be
// buffered by an intermediate hop, and no secret ever lives in the frontend.
const nextConfig: NextConfig = {
  output: "standalone",
  poweredByHeader: false,
  reactStrictMode: true,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
