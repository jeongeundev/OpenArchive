-- 009_auth_tables.sql — 설치 단위 계정·세션과 추출 텍스트 크기 경계
--
-- 한 설치가 한 조직이며 자체 가입은 없다. 계정은 관리자만 만든다. 실무 ECM은 사내
-- 디렉터리 연동 또는 관리자 발급이 일반적이고, 초대 코드는 이 범위에 맞지 않는 SaaS
-- 협업 도구 패턴이다.
--
-- 인증 구현은 hashlib.scrypt와 secrets만 사용한다. 둘 다 Python 표준 라이브러리이므로
-- 외부 인증 의존성과 SBOM 항목을 추가하지 않는다. 평문 비밀번호는 어디에도 저장하지 않는다.

CREATE TABLE users (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text NOT NULL UNIQUE,
  password_hash text NOT NULL,     -- hashlib.scrypt 결과만 저장한다
  is_admin      boolean NOT NULL DEFAULT false,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- is_admin은 계정 관리 권한만 뜻한다. 관리자는 다른 사용자의 private 문서를 볼 수 없다.
-- 시스템 관리자와 문서 열람 권한을 분리해야 ADR-027의 "존재하지 않는 것처럼" 원칙과
-- 기존 열람 술어를 그대로 유지할 수 있다.

CREATE TABLE sessions (
  token      text PRIMARY KEY,     -- secrets.token_urlsafe(32)
  user_id    uuid NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL
);

-- DB는 원본 파일 크기가 아니라 저장되는 추출 텍스트 길이만 제한한다. 500KB는 현재
-- 시연 데이터 최대 문서(약 90KB)의 5배보다 크면서 비정상적으로 큰 본문은 차단하는 값이다.
ALTER TABLE documents
  ADD CONSTRAINT documents_content_length CHECK (length(content) <= 500000);
