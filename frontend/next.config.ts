import type { NextConfig } from "next";

// 백엔드 주소는 환경변수로 덮어쓴다. 기본값은 로컬 uvicorn.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${backendUrl}/api/:path*`,
      },
    ];
  },
};

export default nextConfig;
