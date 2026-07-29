from pathlib import Path
from typing import Any

from app.core.errors import LinkParseError


class RapidOCREngine:
    name = "rapidocr"

    def __init__(self, intra_op_num_threads: int = 3, inter_op_num_threads: int = 1) -> None:
        self._engine: Any = None
        self.intra_op_num_threads = intra_op_num_threads
        self.inter_op_num_threads = inter_op_num_threads

    def available(self) -> bool:
        try:
            import rapidocr  # noqa: F401

            return True
        except ImportError:
            return False

    def _get_engine(self) -> Any:
        if self._engine is None:
            import rapidocr
            from rapidocr import RapidOCR

            model_dir = Path(rapidocr.__file__).parent / "models"
            model_files = {
                "Det.model_path": next(model_dir.glob("*det*.onnx"), None),
                "Cls.model_path": next(model_dir.glob("*cls*.onnx"), None),
                "Rec.model_path": next(model_dir.glob("*rec*.onnx"), None),
                "Rec.rec_keys_path": next(model_dir.glob("*keys*.txt"), None),
            }
            params = {key: str(value) for key, value in model_files.items() if value is not None}
            params.update(
                {
                    "EngineConfig.onnxruntime.intra_op_num_threads": self.intra_op_num_threads,
                    "EngineConfig.onnxruntime.inter_op_num_threads": self.inter_op_num_threads,
                }
            )
            self._engine = RapidOCR(params=params or None)
        return self._engine

    @staticmethod
    def _normalise(result: Any, include_bbox: bool) -> list[dict[str, Any]]:
        if result is None:
            return []
        if hasattr(result, "txts"):
            texts = list(result.txts if result.txts is not None else [])
            boxes = list(result.boxes if result.boxes is not None else [])
            scores = list(result.scores if result.scores is not None else [])
            rows = zip(boxes, texts, scores, strict=False)
        else:
            raw = result[0] if isinstance(result, tuple) else result
            rows = ((item[0], item[1], item[2]) for item in (raw or []))
        blocks = []
        for box, text, score in rows:
            points = box.tolist() if hasattr(box, "tolist") else box
            xs = [point[0] for point in points]
            ys = [point[1] for point in points]
            block: dict[str, Any] = {
                "type": "text",
                "text": str(text),
                "confidence": round(float(score), 6),
            }
            if include_bbox:
                block["bbox"] = [min(xs), min(ys), max(xs), max(ys)]
            blocks.append(block)
        return blocks

    def parse_image(self, path: Path, page: int, include_bbox: bool) -> dict[str, Any]:
        try:
            from PIL import Image

            with Image.open(path) as image:
                width, height = image.size
            result = self._get_engine()(str(path))
            blocks = self._normalise(result, include_bbox)
            return {
                "page": page,
                "width": width,
                "height": height,
                "text": "\n".join(block["text"] for block in blocks),
                "blocks": blocks,
            }
        except Exception as exc:
            raise LinkParseError("OCR_FAILED", f"OCR failed on page {page}: {exc}", 422) from exc
