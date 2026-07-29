from types import SimpleNamespace

import numpy as np

from app.engines.rapidocr_engine import RapidOCREngine


def test_keeps_configured_onnxruntime_thread_limits():
    engine = RapidOCREngine(intra_op_num_threads=2, inter_op_num_threads=1)
    assert engine.intra_op_num_threads == 2
    assert engine.inter_op_num_threads == 1


def test_normalises_current_rapidocr_numpy_output():
    result = SimpleNamespace(
        txts=("hello",),
        scores=(0.9876543,),
        boxes=np.array([[[1, 2], [5, 2], [5, 8], [1, 8]]], dtype=float),
    )
    blocks = RapidOCREngine._normalise(result, include_bbox=True)
    assert blocks == [
        {
            "type": "text",
            "text": "hello",
            "confidence": 0.987654,
            "bbox": [1.0, 2.0, 5.0, 8.0],
        }
    ]
