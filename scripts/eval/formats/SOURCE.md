# 형식별 평가 자료 출처 (#167)

자료는 대한민국 정책브리핑 보도자료 첨부다. 화면의 이용 조건은 **「텍스트에 한하여 공공누리 제1유형(출처표시)」**이고
사진·이미지·일러스트는 별도 허락이 필요하다(2026-10-04 확인). 정부 문서는 거의 모두 기관 로고나 삽화를 품고 있어서
**첨부 파일을 저장소에 넣지 않는다**. `scripts/eval_formats_load.py`가 평가셋(`../formats.json`)의 `fetch`로
`https://www.korea.kr/common/download.do?fileId=<fileId>&tblKey=GMN`에서 받아 이 폴더에 두고(무시됨),
SHA-256이 평가셋과 다르면 멈춘다. 원본이 내려가면 재현이 끊긴다 — 그 경우 평가셋의 해시로 같은 파일인지만 확인할 수 있다.

| 파일 | 기관·보도자료 (newsId) | fileId |
|---|---|---|
| `csat_applications.hwpx` | 교육부·한국교육과정평가원, 「2027학년도 대학수학능력시험 응시원서 접수 결과」 (156780646) | 198547824 |
| `csat_applicants.xlsx` | 같은 보도자료 (별첨1) 지원자 현황 | 198547825 |
| `inflation_v1/inflation_linked_coefficients.xlsx` | 재정경제부, 「2026년 9~10월 물가연동국고채 종목별 연동계수」 (156780239) — 9월판 | 198546007 |
| `inflation_v2/inflation_linked_coefficients.xlsx` | 같은 보도자료 — 10월판 | 198546008 |
| `building_stock.pdf` | 국토교통부, 「전국 건축물 총 7,438,642동 / 43억 75백만㎡」 (156783582) | 198561933 |
| `customs_check_press.pdf` | 관세청, 「국민건강·산업안전·생태계 보전을 위한 수입요건확인 강화 조치 시행」 (156770943) PDF판 — 아래 파생 자료의 원본 | 198508599 |
| `customs_check_infographic.pptx` | 같은 보도자료의 인포그래픽 | 198508600 |
| `mekong_cochairs_statement.docx` | 외교부, 「제14차 한-메콩 외교장관회의(7.23.) 결과」 (156772006) 공동의장 성명(영문) | 198516572 |
| `private/counterfeit_filter_ring.hwpx` | 관세청, 「70억원 상당 해외 유명브랜드 짝퉁 공기청정기 필터 등 밀수·유통조직 검거」 (156769519) | 198502402 |

파생 자료는 적재기가 `customs_check_press.pdf`에서 만든다(`derive`). 노이즈 시드와 저장 시각을 고정해 같은 원본·같은
라이브러리에서는 같은 바이트가 나온다. 라이브러리 판이 다르면 해시가 달라질 수 있어 경고만 하고 계속한다 — 그때의
OCR 결과는 기준선과 다를 수 있다.

| 파일 | 가공 |
|---|---|
| `customs_check_scan.jpg` | 1쪽을 pypdfium2로 300dpi 래스터 → 절반 축소(150dpi) → 1.5° 회전 → 가우스 노이즈(σ 12, seed 167) → 블러 0.6 → JPEG q60 |
| `customs_check_page2_image.pptx` | 2쪽을 같은 방식으로 가공한 그림 한 장만 담은 슬라이드(글상자·노트 없음) — 그림뿐인 슬라이드 사례 |
| `customs_check_scan.txt` (저장소에 있음) | 같은 PDF판 1쪽의 텍스트 레이어(`pdftotext -f 1 -l 1`) — 스캔 OCR 근거의 정답 |

평가셋은 `backend/tests/fixtures/`의 `committee_result.hwp`·`mixed_tax_pages.pdf`도 경로로 참조한다(출처는 그 폴더의
`SOURCE.md`). 이 두 픽스처도 기관 로고를 품고 있다 — 저장소에 이미 있는 픽스처의 처리는 이번 작업 범위 밖이다.

**고르지 못한 것**: 보도자료 약 8천 건의 첨부를 훑었지만 한국어 DOCX는 없었다(DOCX는 전부 영문 자료) — DOCX는 영문
문서를 한국어로 묻는 교차 언어 사례로 둔다. 그림뿐인 실제 PPTX(질병관리청 그래프 등)는 이미지가 공공누리 밖이라
기관 보도자료 텍스트 쪽을 가공한 파생 슬라이드로 대신한다.
