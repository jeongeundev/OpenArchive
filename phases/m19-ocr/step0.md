# Step 0: ocr-ci

이 phase(#135, ADR-052)는 이미지·스캔 PDF를 **tesseract**로 OCR한다. 다음 step부터 테스트가 시스템
바이너리 `tesseract`와 한국어 모델(`kor`)을 요구하므로, CI 러너에 먼저 설치한다.

## 읽어야 할 파일

- `.github/workflows/ci.yml` — 현재 CI (ubuntu-latest, pgvector 서비스 컨테이너, `scripts/check.sh`)
- `/docs/ADR.md` — ADR-052 (결정 1: 엔진은 tesseract `kor`+`eng`)

## 작업

`.github/workflows/ci.yml`의 `check` 잡에 백엔드 의존성 설치 **앞에** 단계 하나를 추가한다.

```yaml
- name: Install OCR engine
  run: sudo apt-get update && sudo apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-kor
```

- 설치 뒤 같은 단계에서 `tesseract --list-langs`를 출력해 `kor`가 있는지 로그에 남긴다
  (`tesseract --list-langs | grep -x kor`로 없으면 실패하게 한다).
- 다른 단계·서비스·캐시 설정은 건드리지 않는다.

## Acceptance Criteria

```bash
python3 -c "import yaml,sys; d=yaml.safe_load(open('.github/workflows/ci.yml')); s=[x.get('run','') for x in d['jobs']['check']['steps']]; i=[n for n,r in enumerate(s) if 'tesseract-ocr-kor' in r]; j=[n for n,r in enumerate(s) if 'pip install' in r]; assert i and j and i[0] < j[0], (i,j); print('ok')"
grep -n "grep -x kor" .github/workflows/ci.yml
```

`yaml` 모듈이 없으면 `backend/.venv/bin/python`으로 실행한다(없으면 `pip install pyyaml` 없이 `grep -n` 두 줄로
순서를 확인해도 된다 — 설치 단계의 줄 번호가 `pip install` 줄보다 작아야 한다).

## 검증 절차

1. 위 AC 커맨드를 실행한다.
2. 이 step은 로컬에서 CI를 돌릴 수 없다. 실제 러너 통과는 PR에서 확인한다 — summary에 그렇게 적는다.
3. `phases/m19-ocr/index.json`의 step 0을 갱신한다 (성공 → completed + summary / 실패 → error / 사용자 개입 → blocked).

## 금지사항

- `scripts/check.sh`에 설치 명령을 넣지 마라. 이유: check.sh는 개발자 맥에서도 도는 검증 명령이고, 시스템
  패키지 설치는 환경 구성이다(맥은 `brew install tesseract tesseract-lang`, 문서는 step 7).
- tessdata를 저장소에 커밋하지 마라. 이유: 모델은 OS 패키지로 설치하는 것이 ADR-052의 결정이다.
- 기존 테스트를 깨뜨리지 마라
