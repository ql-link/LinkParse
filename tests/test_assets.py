from pathlib import Path

from app.core.config import Settings
from app.services.assets import OssAssetStorage


class FakeBucket:
    def __init__(self) -> None:
        self.uploads: list[tuple[str, str, dict]] = []
        self.deleted: list[str] = []

    def put_object_from_file(self, key: str, path: str, headers: dict) -> None:
        self.uploads.append((key, path, headers))

    def sign_url(self, *_args, **_kwargs) -> str:
        raise AssertionError("Public OSS URLs must not be signed")

    def delete_object(self, key: str) -> None:
        self.deleted.append(key)


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        data_dir=tmp_path,
        api_keys=["test"],
        oss_endpoint="https://oss-cn-shanghai.aliyuncs.com",
        oss_access_key_id="test-id",
        oss_access_key_secret="test-secret",
        oss_bucket="qingluo-public",
        oss_object_prefix="LinkRarse",
        oss_public_base_url="https://qingluo-public.oss-cn-shanghai.aliyuncs.com",
    )


def test_upload_returns_stable_public_url_and_rewrites_outputs(tmp_path):
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "figure one.png"
    image_path.write_bytes(b"png")
    bucket = FakeBucket()
    storage = OssAssetStorage(_settings(tmp_path), bucket=bucket)

    assets, replacements = storage.upload_files(
        "req_demo",
        [image_path],
        kind="embedded_image",
        relative_to=tmp_path,
        pages={"images/figure one.png": 2},
    )

    assert len(bucket.uploads) == 1
    assert bucket.uploads[0][0].startswith("LinkRarse/req_demo/")
    assert assets[0]["url"].startswith(
        "https://qingluo-public.oss-cn-shanghai.aliyuncs.com/LinkRarse/"
    )
    assert "%20" in assets[0]["url"]
    assert assets[0]["expires_at"] is None
    assert assets[0]["page"] == 2
    rewritten = storage.rewrite_outputs(
        {"markdown": "![figure](images/figure one.png)"}, replacements
    )
    assert assets[0]["url"] in rewritten["markdown"]

    assert storage.delete_assets(assets) == 1
    assert bucket.deleted == [bucket.uploads[0][0]]
