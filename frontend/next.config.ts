import type { NextConfig } from "next";

// 백엔드 주소는 환경변수로 덮어쓴다. 기본값은 로컬 uvicorn.
const backendUrl = process.env.BACKEND_URL ?? "http://localhost:8000";

// 빌드 산출물을 백엔드 패키지에 동봉할 때만 정적 export로 뽑는다. 개발 서버는 아래
// rewrites 프록시가 필요하므로 기본 모드를 유지한다 (STATIC_EXPORT=1 npm run build).
const staticExport = process.env.STATIC_EXPORT === "1";

const nextConfig: NextConfig = {
  // 빌드 ID를 고정한다. Next는 빌드마다 새 ID를 만드는데, 산출물을 저장소에 커밋하는
  // 구성(ADR-041)에서는 그것만으로 파일 수십 개가 매번 "수정됨"이 되어 소스가 그대로인데도
  // diff가 쌓인다. 캐시 무효화는 콘텐츠 해시가 붙은 청크 파일명이 이미 하고 있다.
  generateBuildId: () => "openarchive",
  ...(staticExport
    ? { output: "export" as const }
    : {
        // 동봉해서 서빙할 때는 API가 같은 오리진에 있으므로 프록시가 필요 없다.
        // 정적 export는 rewrites를 무시하므로(Next 16) 아예 넣지 않는다.
        async rewrites() {
          return [
            {
              source: "/api/:path*",
              destination: `${backendUrl}/api/:path*`,
            },
          ];
        },
      }),
};

export default nextConfig;
