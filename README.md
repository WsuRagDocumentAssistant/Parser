# WSU 통합 문서 파서

Python 3.11 이상에서 HWPX, DOCX, XLSX, PDF를 하나의 API로 읽습니다.
organization의 `DocumentModel` 필드·타입 및 한글 종류/역할에 맞춰 반환합니다.
다른 저장소나 기존 서비스 코드는 변경하지 않습니다. COM/Office 설치가 필요 없습니다.
텍스트 파일과 JSON은 UTF-8입니다.

## 설치 및 실행

```powershell
python -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -r requirements-lock.txt
& .\.venv\Scripts\python.exe -m pip install --no-deps .
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe -m document_parser '보고서.hwpx' --output output/report
```

Ubuntu에서도 `python3 -m venv .venv`, `.venv/bin/python`으로 같은 명령을 실행합니다.
설치 후 `document-parse` 명령도 제공합니다.
IDE 실행 버튼은 `doc/`에 네 포맷 파일을 넣고 루트 `main.py`를 실행합니다.
출력은 `output/<원본 파일명>/`이며, 한 파일이 실패해도 나머지는 처리합니다.
하위 폴더와 `~$` 잠금 파일은 제외합니다. 일부 실패/입력 없음은 종료 코드 1입니다.
개별 CLI는 성공 0, 오류 2입니다. 부분 추출 여부는 구조 결과의 상태·진단을 확인하세요.

## 공통 API

```python
from document_parser import DocumentParser, parse

model = parse('보고서.pdf')  # 확장자로 자동 선택, OCR 기본 비활성화
data = model.to_dict()      # file, images, blocks
model.export('output/report')

for block in model.searchable_blocks():
    print(block.kind, block.role, block.heading_path_text, block.text)
for table in model.tables():  # 중첩 표 포함
    print(table.rows, table.cols, table.cells, table.markdown)

# 기존 포맷 옵션은 해당 파서에만 전달합니다.
parser = DocumentParser(hwpx={'page_mode': 'structural'}, pdf={'ocr': None})
model = parser.parse('계산.xlsx', image_dir='output/images')
```

수신 측 사용 예시:

```python
from document_parser import parse
from ragmodul import chunk

parsed = parse('문서.docx', image_dir='images')
chunked = chunk(parsed)
```

기존 `ragmodul.parse()`는 기존 HWPX 서비스를 계속 호출합니다.
새 파서의 도입은 위 공통 API에서 반환한 객체를 청킹 단계에 전달하는 방식입니다.
`hwpx` 모듈을 덮어쓰거나 다른 저장소의 import를 변경하지 않습니다.
객체의 필드·직렬화 형태가 호환되며, 외부 패키지의 클래스와 클래스 identity가 같지는 않습니다.

## 결과와 데이터 보존

| 결과 | 내용 |
| --- | --- |
| `document.json` | organization 계약의 `file`, `images`, `blocks` |
| `structure/document.json` | 기존 추출 구조 전체: 페이지 근거, 수식, 서식, 진단, 자산 |
| `structure/texts.*`, `tables.*`, `headings.*` | 텍스트/표/제목의 JSON 및 읽기용 결과 |
| `structure/images.json`, `metadata.json` | 이미지 위치와 문서 메타데이터 |
| `<문서 stem>/images/` | 계약 객체가 참조하는 실제 이미지 파일 |

`model.structure`는 포맷별 원본 구조 객체입니다. JSON 계약에는 추가하지 않습니다.
표 안의 문단·그림·중첩 표는 `table.cells` 및 `table.children`에 넣어 본문 중복을 막습니다.
제목 ID 경로는 실제 반환 블록 ID로 연결합니다. 시트명/PDF 목차에 독립 문단이 없으면
제목 블록을 구성하여 기존 청킹 코드가 계층을 읽을 수 있게 합니다.
행·열 좌표는 셀 객체에서 0부터, Markdown의 주소 표시에서는 1부터 셉니다.
Markdown은 행·열 격자이며 병합 셀의 값을 덮인 위치에도 채웁니다.
XLSX에서 행 수 × 열 수가 100,000을 넘는 표만 희소 셀 주소 목록으로 남기며
헤더·레코드 추론을 생략합니다. 다른 포맷의 격자 크기는 1,000,000칸으로 제한합니다.
기존 조직의 표 분류·헤더·레코드 안정성 규칙을 공통으로 적용합니다.
제목상자는 `table.kind='제목상자'`, `table.title` 및 실제 제목 경로를 제공합니다.
수치 판정은 헤더를 제외한 수치열 기준이며 근거가 없으면 `판정불가`입니다.
표의 모든 셀을 보존하므로 구조를 확신하지 못한 표도 Markdown으로 청킹할 수 있습니다.
헤더·제목 분류는 휴리스틱입니다. 기존 파이프라인의 전체 depth 보정과 모든 목차 표현을
동일하게 재현하지는 않으며, 청크 개수·경계가 기존 결과와 같다는 보장은 없습니다.
HWPX는 기존 조직의 문서 내 스타일 클러스터 규칙으로 문단의 글자 크기·굵기·
빈도·길이·후행 블록을 비교하여 번호 없는 문단 제목도 찾습니다. 제목상자 아래의
문단 제목은 `□`/`○` 등 마커와 개요 번호에 따라 계층을 보정합니다.
번호·기호만 있는 일반 목록은 자동으로 제목으로 바꾸지 않습니다.
자동 추론 제목은 공백 정리 후 120자 이하이고 문장 종결 부호가 없는 경우로 제한합니다.
명시적 OUTLINE 및 사용자 스타일 매핑은 유지합니다. 제목과 제목 경로의 줄바꿈·
연속 공백은 공백 하나로 정리하며, 원본 문단·셀 텍스트는 `model.structure`에 보존합니다.

`parse(..., unpack_dir=..., cleanup=..., recover_excluded=...)`는 기존 서비스의
키워드 호출을 허용합니다. 이 파서는 ZIP을 메모리에서 읽고 표를 제외하지 않으므로
이 세 옵션은 파일 추출·삭제·복구를 실행하지 않습니다. `cleanup=False`로
압축 해제 폴더를 얻을 수는 없으며, 원본 구조는 `model.structure`로 확인합니다.

`image_dir`/`export()`를 사용하면 실제 이미지 경로와 `document_images` 목록을 제공합니다.
같은 원본 그림의 반복 사용은 파일/목록을 공유하고 블록 위치는 각각 유지합니다.
표 안 이미지도 파일로 저장하지만 `document_images`에는 독립 그림 블록만 넣습니다.
`image_dir` 없이 얻은 `images[].path`는 패키지 내부 경로일 수 있으므로 디스크 경로로 쓰지 마세요.
재실행은 결과 파일을 교체하고 이전 이미지 잔재는 삭제하지 않습니다.

PDF의 페이지는 원본 물리 페이지입니다. HWPX/DOCX 페이지는 추정 또는 미확정이고,
XLSX 인쇄 페이지는 계산하지 않습니다. 상태는 `model.structure.page_status` 및
각 블록 `location`에서 확인합니다. 스캔 PDF는 OCR 없이 텍스트를 얻지 못할 수 있으며
진단 정보로 표시합니다.

## PDF OCR

기본 설치는 OCR 의존성을 제외하고 기본 실행은 OCR을 호출하지 않습니다.
`main.py`에 활성화 코드를 주석으로 남겼습니다. 필요한 경우에만 다음을 실행합니다.

```powershell
& .\.venv\Scripts\python.exe -m pip install -r requirements-ocr.txt
& .\.venv\Scripts\python.exe -m document_parser scan.pdf --ocr paddle --ocr-model server
# 한국어 문서: 서버 탐지 모델 + 한국어 전용 인식 모델
& .\.venv\Scripts\python.exe -m document_parser korean.pdf --ocr paddle --ocr-model korean
```

`server`는 `PP-OCRv5_server_det` + `PP-OCRv5_server_rec`, `device='cpu'`입니다.
`korean`은 같은 서버 탐지 모델에 `korean_PP-OCRv5_mobile_rec`를 연결합니다.
공식 서버 인식 모델의 지원 언어는 중국어 간체/번체·영어·일본어이며 한국어는 포함되지 않습니다.
한국어 서버 인식 모델이 있다고 가정하지 않습니다. [공식 모델 목록](https://www.paddleocr.ai/main/en/version3.x/pipeline_usage/OCR.html)
OCR 모델은 첫 사용 시 `.cache/paddlex`에 내려받아 재사용합니다. 모델 가중치는 Git에 포함하지 않습니다.
텍스트 레이어가 없는 페이지에서만 OCR을 실행하며 기본 DPI는 200, 최소 신뢰도는 0.5입니다.
`--ocr-dpi`, `--ocr-min-confidence`로 조절할 수 있고 인식 모델·좌표·신뢰도는 구조 결과에 남습니다.
CPU 메모리 사용을 제한하기 위해 탐지 입력의 긴 변은 기본 960px로 축소합니다.
작은 글씨의 탐지 정확도와 메모리는 `--ocr-det-limit-side-len`(64~2048)으로 조절합니다.
인식용 잘라낸 영역은 원본 렌더링에서 가져옵니다.
실제 CPU 서버 OCR 검사는 `PDF_PARSER_TEST_SERVER_OCR=1`로 켭니다.

암호 PDF는 `--password-file <UTF-8 파일>` 또는 `parse(path, password=...)`로 처리합니다.
암호는 출력에 저장하지 않습니다.

## 검증 및 계약 출처

```powershell
$env:PYTHONUTF8 = '1'
& .\.venv\Scripts\python.exe -m unittest discover -s tests -t . -q
```

기존 네 포맷 회귀 검사와 공통 타입·UTF-8·직렬화·중첩 표·이미지 소유·OCR 미사용 검사를 포함합니다.
사용자 실제 HWPX 검사와 실제 OCR 모델 검사는 명시적인 환경변수가 있어야 실행합니다.
조직 저장소 사본은 배포하지 않으며, `.reference/`가 있으면 원본 필드 정의와
실제 청킹 코드에 네 포맷 결과를 전달하는 검사도 실행합니다.
공통 표 처리에 필요한 기존 `table_hierarchy` 모듈 20개는 아래 커밋에서 재사용하여
`document_parser/table_hierarchy`에 포함했습니다. 참조 저장소는 수정하지 않았습니다.

- [DocumentModel 타입 원본 — HwpxParser_sub, 8056258](https://github.com/WsuRagDocumentAssistant/HwpxParser_sub/blob/8056258068dbef1afd46b657f824df6eabda5a7a/hwpx/analysis/document_model.py)
- [표 분류·헤더·레코드 처리 원본 — HwpxParser_sub, 8056258](https://github.com/WsuRagDocumentAssistant/HwpxParser_sub/tree/8056258068dbef1afd46b657f824df6eabda5a7a/hwpx/analysis/table_hierarchy)
- [문단 스타일 클러스터 원본 — HwpxParser_sub, 8056258](https://github.com/WsuRagDocumentAssistant/HwpxParser_sub/blob/8056258068dbef1afd46b657f824df6eabda5a7a/hwpx/analysis/add_document_blocks_to_json.py)
- [실제 파서 수신 코드 — RagModuls, 541ee81](https://github.com/WsuRagDocumentAssistant/RagModuls/blob/541ee8116452723389b0778a3dfe689bb84a357d/ragmodul/service/parser_service.py)
- [실제 청킹 소비 코드 — RagModuls, 541ee81](https://github.com/WsuRagDocumentAssistant/RagModuls/blob/541ee8116452723389b0778a3dfe689bb84a357d/ragmodul/service/chunker_service.py)
