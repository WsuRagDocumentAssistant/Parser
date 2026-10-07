"""IDE run button: parse supported files directly inside doc/."""
import json
from pathlib import Path

from document_parser import DocumentParser


def main():
    root = Path(__file__).resolve().parent
    inputs = sorted(p for p in (root / "doc").glob("*") if p.is_file()
                    and p.suffix.lower() in DocumentParser.formats and not p.name.startswith("~$"))
    if not inputs:
        print("doc/에 HWPX, DOCX, XLSX 또는 PDF 파일을 넣으세요.")
        return 1
    failed = False
    # PDF 기본값: OCR 비활성화. 텍스트 레이어만 추출합니다.
    parser = DocumentParser(pdf={"ocr": None})
    # OCR 필요 시 [ocr] 의존성을 설치하고 아래 두 줄로 교체하세요.
    # from pdf_parser import PaddleOcr
    # parser = DocumentParser(pdf={"ocr": PaddleOcr(model="server")})
    # 한국어 문서는 서버 탐지 + 한국어 인식: PaddleOcr(model="korean")
    for source in inputs:
        try:
            result = parser.parse(source).export(root / "output" / source.name)
            print(json.dumps({"input": source.name, "output": str(result)}, ensure_ascii=False))
        except (OSError, ValueError, TypeError, ImportError) as exc:
            failed = True
            print(json.dumps({"input": source.name, "error": str(exc)}, ensure_ascii=False))
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
