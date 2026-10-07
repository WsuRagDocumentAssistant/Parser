"""PP-OCRv5 server detection/recognition on CPU; lazy loading, optional Korean recognition."""
from __future__ import annotations

import math
import os
from pathlib import Path


class PaddleOcr:
    detection_model = "PP-OCRv5_server_det"
    recognition_model = "PP-OCRv5_server_rec"

    def __init__(self, *, dpi=200, min_confidence=0.5, model="server", det_limit_side_len=960):
        if model not in {"server", "korean"}:
            raise ValueError("OCR model must be server or korean")
        if not isinstance(dpi, int) or isinstance(dpi, bool) or not 72 <= dpi <= 600:
            raise ValueError("OCR dpi must be an integer between 72 and 600")
        if not math.isfinite(min_confidence) or not 0 <= min_confidence <= 1:
            raise ValueError("OCR min_confidence must be between 0 and 1")
        if not isinstance(det_limit_side_len, int) or isinstance(det_limit_side_len, bool) or not 64 <= det_limit_side_len <= 2048:
            raise ValueError("OCR det_limit_side_len must be an integer between 64 and 2048")
        self.dpi, self.min_confidence = dpi, min_confidence
        self.recognition_model = "korean_PP-OCRv5_mobile_rec" if model == "korean" else "PP-OCRv5_server_rec"
        self.det_limit_side_len = det_limit_side_len
        self._engine = None

    def recognize(self, image):
        os.environ.setdefault("PADDLE_PDX_CACHE_HOME", str(Path.cwd() / ".cache" / "paddlex"))
        os.environ.setdefault("PADDLE_PDX_MODEL_SOURCE", "bos")
        os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
        try:
            import numpy as np
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise RuntimeError("Install OCR dependencies: python -m pip install -r requirements-ocr.txt") from exc
        if self._engine is None:
            self._engine = PaddleOCR(
                device="cpu",
                text_detection_model_name=self.detection_model,
                text_recognition_model_name=self.recognition_model,
                # CPU server detection on full-resolution pages exhausted memory in validation.
                text_det_limit_type="max", text_det_limit_side_len=self.det_limit_side_len,
                use_doc_orientation_classify=False, use_doc_unwarping=False,
                use_textline_orientation=False, enable_mkldnn=False, cpu_threads=4,
                text_rec_score_thresh=self.min_confidence,
            )
        rgb = image.convert("RGB")
        try:
            # PaddleOCR ndarray inputs use OpenCV's BGR channel convention.
            pixels = np.asarray(rgb)[:, :, ::-1].copy()
        finally:
            rgb.close()
        lines = []
        for result in self._engine.predict(pixels):
            texts, scores, polygons = result["rec_texts"], result["rec_scores"], result["rec_polys"]
            if not len(texts) == len(scores) == len(polygons):
                raise ValueError("Inconsistent PaddleOCR result lengths")
            for text, confidence, polygon in zip(texts, scores, polygons):
                confidence = float(confidence)
                points = [[float(x), float(y)] for x, y in polygon]
                if not (math.isfinite(confidence) and 0 <= confidence <= 1 and len(points) >= 3
                        and all(math.isfinite(v) for point in points for v in point)):
                    raise ValueError("Invalid PaddleOCR confidence or polygon")
                if text.strip() and confidence >= self.min_confidence:
                    lines.append({"text": str(text), "confidence": confidence, "polygon": points})
        return lines
