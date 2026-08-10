from contextlib import contextmanager

import pytest
from redis.exceptions import RedisError

from app.core.config import Settings
from app.core.errors import ConcurrencyLimitReached
from app.services.concurrency import ConcurrencyLimiter
from app.services.parser import DocumentParser


class FakeRedis:
    def __init__(self):
        self.members: dict[str, set[str]] = {}

    def eval(self, script, _key_count, key, *args):
        members = self.members.setdefault(key, set())
        if "ZREMRANGEBYSCORE" in script:
            limit = int(args[2])
            member = args[3]
            if len(members) >= limit:
                return 0
            members.add(member)
            return 1
        members.discard(args[0])
        return 1

    def zremrangebyscore(self, key, _minimum, _maximum):
        self.members.setdefault(key, set())

    def zcard(self, key):
        return len(self.members.setdefault(key, set()))


class RecordingLimiter:
    def __init__(self):
        self.calls: list[str] = []

    @contextmanager
    def slot(self, engine):
        self.calls.append(engine)
        yield


class UnavailableRedis:
    def eval(self, *_args):
        raise RedisError("unavailable")


class FakeOcr:
    def available(self):
        return True

    def parse_image(self, _path, page, _include_bbox):
        return {"page": page, "text": "ok", "blocks": []}


class FakeStructured:
    image_pages = {}
    last_metadata = {}

    def parse(self, _path, _output_dir, _formats, _include_images):
        return {
            "text": "structured",
            "markdown": "<!-- ODL_PAGE:1 -->\n\nstructured",
            "json": {},
        }, []


def test_distributed_limits_are_independent_and_release_slots(tmp_path):
    settings = Settings(
        data_dir=tmp_path,
        ocr_max_concurrency=1,
        opendataloader_max_concurrency=2,
        concurrency_wait_seconds=0,
    )
    limiter = ConcurrencyLimiter(settings)
    limiter.redis = FakeRedis()

    with limiter.slot("rapidocr"):
        status = limiter.describe()
        assert status["engines"]["rapidocr"] == {"active": 1, "limit": 1}
        with pytest.raises(ConcurrencyLimitReached):
            with limiter.slot("rapidocr"):
                pass
        with limiter.slot("opendataloader"):
            with limiter.slot("opendataloader"):
                assert limiter.describe()["engines"]["opendataloader"]["active"] == 2

    assert limiter.describe()["engines"]["rapidocr"]["active"] == 0
    with limiter.slot("rapidocr"):
        pass


def test_redis_failure_fails_closed_instead_of_using_per_process_slots(tmp_path):
    settings = Settings(data_dir=tmp_path, concurrency_wait_seconds=0)
    limiter = ConcurrencyLimiter(settings)
    limiter.redis = UnavailableRedis()

    with pytest.raises(ConcurrencyLimitReached):
        with limiter.slot("rapidocr"):
            pass


def test_parser_uses_separate_slots_for_ocr_and_opendataloader(tmp_path):
    limiter = RecordingLimiter()
    parser = DocumentParser(
        Settings(data_dir=tmp_path),
        concurrency_limiter=limiter,
    )
    parser._ocr = FakeOcr()
    parser._structured = FakeStructured()
    image_path = tmp_path / "image.png"
    image_path.write_bytes(b"image")

    assert parser._ocr_image(image_path, 1, True)["text"] == "ok"
    parser._analyze_quality = lambda *_args, **_kwargs: {
        "status": "PASSED",
        "page_provenance_valid": True,
        "ocr_required_pages": [],
    }
    outputs, _ = parser._parse_pdf_pipeline(
        image_path,
        {"text"},
        True,
        None,
        page_count=1,
    )

    assert outputs["text"] == "structured"
    assert limiter.calls == ["rapidocr", "opendataloader"]
