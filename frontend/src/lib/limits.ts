// 백엔드 Settings.max_upload_mb 기본값(50, 십진 MB)과 같은 값·문구.
// 선검사는 대형 파일의 전송 비용을 아끼기 위한 것이고, 경계의 최종 권위는 백엔드의 413이다 —
// 정적 빌드라 운영자가 바꾼 설정값을 화면이 읽을 수 없다.
export const MAX_UPLOAD_BYTES = 50_000_000;
export const UPLOAD_TOO_LARGE = "업로드 파일은 50MB를 넘을 수 없습니다.";

// 백엔드 Settings.trash_retention_days 기본값(30)과 같은 값. 정적 빌드라 운영자가 바꾼 보존 기간을
// 화면이 읽지 못한다 — 실제 영구 삭제 예정일은 휴지통 목록의 purge_at(서버 계산)이 권위다.
export const TRASH_NOTICE = "휴지통으로 옮깁니다. 30일 뒤 영구 삭제됩니다.";
