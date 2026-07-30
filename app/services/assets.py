import base64
import logging
import mimetypes
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import oss2
from oss2.exceptions import OssError

from app.core.config import Settings
from app.core.errors import LinkParseError

logger = logging.getLogger("linkparse.assets")


class OssAssetStorage:
    def __init__(self, settings: Settings, bucket: Any | None = None) -> None:
        self.settings = settings
        self._bucket = bucket

    @property
    def configured(self) -> bool:
        return all(
            (
                self.settings.oss_endpoint,
                self.settings.oss_access_key_id,
                self.settings.oss_access_key_secret,
                self.settings.oss_bucket,
            )
        )

    @property
    def bucket(self):
        if not self.configured:
            raise LinkParseError(
                "STORAGE_UNAVAILABLE",
                "Image output requires Aliyun OSS configuration",
                503,
            )
        if self._bucket is None:
            auth = oss2.Auth(
                self.settings.oss_access_key_id,
                self.settings.oss_access_key_secret,
            )
            self._bucket = oss2.Bucket(
                auth,
                self.settings.oss_endpoint,
                self.settings.oss_bucket,
                connect_timeout=10,
            )
        return self._bucket

    def available(self) -> bool:
        if not self.configured:
            return False
        try:
            self.bucket.get_bucket_info()
            return True
        except OssError:
            return False

    def upload_files(
        self,
        request_id: str,
        paths: list[Path],
        *,
        kind: str,
        relative_to: Path | None = None,
        pages: dict[str, int] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        if not paths:
            return [], {}
        safe_request_id = re.sub(r"[^A-Za-z0-9._-]", "_", request_id)[:128]
        scope = f"{self.settings.oss_object_prefix.strip('/')}/{safe_request_id}/{uuid.uuid4().hex}"
        expires_seconds = max(
            self.settings.oss_url_ttl_hours,
            self.settings.job_result_ttl_hours + 24,
        ) * 3600
        expires_at = (
            None
            if self.settings.oss_public_base_url
            else datetime.now(UTC) + timedelta(seconds=expires_seconds)
        )
        uploaded_keys: list[str] = []
        assets: list[dict[str, Any]] = []
        replacements: dict[str, str] = {}
        try:
            for index, path in enumerate(sorted(paths), start=1):
                filename = path.name
                object_key = f"{scope}/{index:04d}-{filename}"
                media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
                if not media_type.startswith("image/"):
                    continue
                self.bucket.put_object_from_file(
                    object_key,
                    str(path),
                    headers={"Content-Type": media_type},
                )
                uploaded_keys.append(object_key)
                asset_id = self.encode_object_key(object_key)
                if self.settings.oss_public_base_url:
                    url = (
                        f"{self.settings.oss_public_base_url.rstrip('/')}"
                        f"/{quote(object_key, safe='/')}"
                    )
                else:
                    url = self.bucket.sign_url("GET", object_key, expires_seconds, slash_safe=True)
                source_ref = (
                    path.relative_to(relative_to).as_posix()
                    if relative_to is not None
                    else filename
                )
                page = (pages or {}).get(source_ref)
                assets.append(
                    {
                        "id": asset_id,
                        "kind": kind,
                        "filename": filename,
                        "media_type": media_type,
                        "size_bytes": path.stat().st_size,
                        "url": url,
                        "expires_at": expires_at.isoformat() if expires_at else None,
                        "page": page,
                    }
                )
                replacements[source_ref] = url
        except Exception as exc:
            for object_key in uploaded_keys:
                try:
                    self.bucket.delete_object(object_key)
                except OssError:
                    logger.warning("asset_rollback_failed object_key=%s", object_key)
            if isinstance(exc, LinkParseError):
                raise
            raise LinkParseError(
                "STORAGE_UNAVAILABLE", "Failed to upload images to OSS", 503
            ) from exc
        return assets, replacements

    def delete_assets(self, assets: object) -> int:
        if not isinstance(assets, list) or not assets or not self.configured:
            return 0
        deleted = 0
        for asset in assets:
            if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
                continue
            try:
                object_key = self.decode_object_key(asset["id"])
                self.bucket.delete_object(object_key)
                deleted += 1
            except (OssError, ValueError):
                logger.warning("asset_delete_failed asset_id=%s", asset.get("id"))
        return deleted

    @staticmethod
    def rewrite_outputs(value: Any, replacements: dict[str, str]) -> Any:
        if isinstance(value, str):
            for source, url in replacements.items():
                value = value.replace(source, url)
            return value
        if isinstance(value, list):
            return [OssAssetStorage.rewrite_outputs(item, replacements) for item in value]
        if isinstance(value, dict):
            return {
                key: OssAssetStorage.rewrite_outputs(item, replacements)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def encode_object_key(object_key: str) -> str:
        return base64.urlsafe_b64encode(object_key.encode()).decode().rstrip("=")

    @staticmethod
    def decode_object_key(asset_id: str) -> str:
        try:
            padding = "=" * (-len(asset_id) % 4)
            object_key = base64.urlsafe_b64decode(asset_id + padding).decode()
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError("Invalid asset id") from exc
        if not object_key or object_key.startswith("/") or ".." in Path(object_key).parts:
            raise ValueError("Invalid asset id")
        return object_key
