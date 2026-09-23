// 백엔드 Settings.max_upload_mb 기본값(50, 십진 MB)과 같은 값·문구.
// 선검사는 대형 파일의 전송 비용을 아끼기 위한 것이고, 경계의 최종 권위는 백엔드의 413이다 —
// 정적 빌드라 운영자가 바꾼 설정값을 화면이 읽을 수 없다.
export const MAX_UPLOAD_BYTES = 50_000_000;
export const UPLOAD_TOO_LARGE = "업로드 파일은 50MB를 넘을 수 없습니다.";
