/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,

  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL,
    NEXT_PUBLIC_BACKEND_URL: process.env.NEXT_PUBLIC_BACKEND_URL,
    NEXT_PUBLIC_BACKEND_API_KEY: process.env.NEXT_PUBLIC_BACKEND_API_KEY,
  },

  async rewrites() {
    const backend =
      process.env.NEXT_PUBLIC_BACKEND_URL ||
      process.env.BACKEND_URL ||
      process.env.BACKEND_API_URL ||
      "";

    if (!backend) {
      return [];
    }

    const base = backend.replace(/\/$/, "");

    return [{ source: "/api/:path*", destination: `${base}/api/:path*` }];
  },

  typescript: {
    ignoreBuildErrors: true,
  },
  eslint: {
    ignoreDuringBuilds: true,
  },
};

module.exports = nextConfig;
