# 픽스처 출처

| 파일 | 출처 | 이용 조건 |
|---|---|---|
| `committee_result.hwp`, `committee_result.hwpx` | 방송미디어통신위원회, 「2026년 제38차 위원회 결과」 보도자료(2026-09-28), 대한민국 정책브리핑 <https://www.korea.kr/briefing/pressReleaseView.do?newsId=156783330> | 공공누리 제1유형(출처표시) |
| `tax_administration.hwp`, `tax_administration.hwpx` | 국세청, 「국민의 목소리를 담아 새롭게 도약하는 국세행정」 보도자료(2026-09-28), 대한민국 정책브리핑 <https://www.korea.kr/briefing/pressReleaseView.do?newsId=156783334> | 공공누리 제1유형(출처표시) |

각 쌍은 같은 문서를 한글이 두 형식으로 저장한 실제 파일이다. 원본 그대로 두고 고치지 않는다 —
HWP 파서 테스트는 한 쌍의 추출 결과가 같은지를 본다.

| 파일 | 출처 | 이용 조건 |
|---|---|---|
| `office_budget.xlsx` | 이 저장소에서 직접 작성. macOS Numbers로 만든 표를 Excel 형식으로 내보냈다(2026-09-29). 합계 셀은 `=SUM` 수식이다 | 저장소 라이선스 |
| `office_briefing.pptx` | 이 저장소에서 직접 작성. macOS Keynote로 만든 슬라이드 2장(발표자 노트 포함)을 PowerPoint 형식으로 내보냈다(2026-09-29) | 저장소 라이선스 |

두 파일은 실제 앱이 저장한 파일이라 수식 셀의 계산값이 캐시돼 있다 — 계산 엔진 없이 쓴 파일(openpyxl)과의
차이는 `test_parsing.py`가 따로 고정한다. Numbers는 「내보내기 요약」 시트를 덧붙인다.

| 파일 | 출처 | 이용 조건 |
|---|---|---|
| `scan_tax_page1.jpg`, `scan_tax_pages.pdf` | 위 국세청 보도자료의 PDF판(정책브리핑 첨부)을 이 저장소에서 스캔본처럼 가공(2026-09-29): `pdftoppm -r 300` → 150dpi 축소 · 1.5° 기울임 · 가우스 노이즈 · 블러 0.6 · JPEG q60. PDF는 가공한 1·2쪽 이미지만 담아 텍스트 레이어가 없다 | 공공누리 제1유형(출처표시) |
| `scan_tax_page1.txt`, `scan_tax_pages.txt` | 같은 PDF판의 텍스트 레이어(`pdftotext`) — OCR 정확도 테스트의 정답 | 공공누리 제1유형(출처표시) |

정답의 읽기 순서는 PDF 텍스트 레이어를 따른다. OCR 결과와는 공백·줄바꿈이 다르므로 테스트는 공백을 지우고
비교한다. 가공 전 원본 기준의 엔진 실측(tesseract·EasyOCR·PaddleOCR)은 #135 코멘트에 있다.
