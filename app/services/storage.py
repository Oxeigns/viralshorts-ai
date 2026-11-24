from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import urlparse

from app.config import get_settings
from app.services.config_store import get_setting

logger = logging.getLogger(__name__)


async def object_storage_ready(session) -> bool:
    bucket = await get_setting(session, "object_storage_bucket")
    access = await get_setting(session, "storage_access_key")
    secret = await get_setting(session, "storage_secret_key")
    return bool(bucket and access and secret)


async def upload_file(session, local_path: str, key: str | None = None) -> str:
    path = Path(local_path)
    if not path.exists():
        raise FileNotFoundError(local_path)
    if not await object_storage_ready(session):
        return str(path)
    try:
        import boto3
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("boto3 is required for object storage") from exc
    endpoint = await get_setting(session, "object_storage_url")
    bucket = await get_setting(session, "object_storage_bucket")
    access = await get_setting(session, "storage_access_key")
    secret = await get_setting(session, "storage_secret_key")
    object_key = key or f"renders/{path.name}"
    client = boto3.client(
        "s3",
        endpoint_url=endpoint or None,
        aws_access_key_id=access,
        aws_secret_access_key=secret,
    )
    extra = {"ContentType": "video/mp4"} if path.suffix.lower() == ".mp4" else {}
    client.upload_file(str(path), bucket, object_key, ExtraArgs=extra)
    if endpoint:
        host = urlparse(endpoint).geturl().rstrip("/")
        return f"{host}/{bucket}/{object_key}"
    return f"s3://{bucket}/{object_key}"


def local_media_path(name: str) -> Path:
    return get_settings().render_root / name
