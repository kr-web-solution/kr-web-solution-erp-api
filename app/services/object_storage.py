"""S3-compatible object storage for uploaded files."""

from __future__ import annotations

import logging
from functools import lru_cache

from botocore.exceptions import ClientError
from fastapi import HTTPException

from app.core.config import settings

logger = logging.getLogger("kr.storage")


def storage_enabled() -> bool:
    return (settings.STORAGE_BACKEND or "").lower() == "s3"


def endpoint_url() -> str:
    raw = (settings.S3_ENDPOINT or "").strip()
    if not raw:
        raise HTTPException(status_code=500, detail="Object storage endpoint is not configured")
    if not raw.startswith("http://") and not raw.startswith("https://"):
        raw = f"https://{raw}"
    return raw.rstrip("/")


def _cors_origins() -> list[str]:
    raw = settings.S3_CONSOLE_CORS_ORIGINS or ""
    origins = [part.strip() for part in raw.split(",") if part.strip()]
    for origin in (settings.FRONTEND_URL, "http://localhost:3000", "http://127.0.0.1:3000"):
        if origin and origin not in origins:
            origins.append(origin)
    return origins


@lru_cache(maxsize=1)
def client():
    import boto3
    from botocore.client import Config

    return boto3.client(
        "s3",
        endpoint_url=endpoint_url(),
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION or "ap-south-1",
        config=Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
        ),
    )


def _bucket_exists(name: str) -> bool:
    try:
        client().head_bucket(Bucket=name)
        return True
    except ClientError:
        return False


def _create_bucket(name: str) -> None:
    if _bucket_exists(name):
        logger.info("Object storage bucket already exists: %s", name)
        return
    region = settings.S3_REGION or "ap-south-1"
    kwargs = {"Bucket": name}
    if region and region != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": region}
    try:
        client().create_bucket(**kwargs)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
            logger.info("Object storage bucket already exists: %s", name)
            return
        if "CreateBucketConfiguration" in kwargs:
            client().create_bucket(Bucket=name)
        else:
            raise
    logger.info("Created object storage bucket: %s", name)


def _apply_cors(name: str) -> None:
    origins = _cors_origins()
    if not origins:
        return
    try:
        client().put_bucket_cors(
            Bucket=name,
            CORSConfiguration={
                "CORSRules": [
                    {
                        "AllowedOrigins": origins,
                        "AllowedMethods": ["GET", "PUT", "POST", "DELETE", "HEAD"],
                        "AllowedHeaders": ["*"],
                        "ExposeHeaders": ["ETag", "Content-Length", "Content-Type"],
                        "MaxAgeSeconds": 3600,
                    }
                ]
            },
        )
    except ClientError as exc:
        logger.warning("Could not set CORS on bucket %s: %s", name, exc.response.get("Error", {}).get("Code"))


def ensure_buckets() -> None:
    """Create the private and public buckets when object storage is enabled."""
    if not storage_enabled():
        return
    private = settings.S3_BUCKET
    public = settings.S3_PUBLIC_BUCKET
    if not private:
        raise RuntimeError("S3_BUCKET is not configured")
    _create_bucket(private)
    _apply_cors(private)
    if public and public != private:
        _create_bucket(public)
        _apply_cors(public)


def put_bytes(key: str, data: bytes, content_type: str, *, public: bool = False) -> None:
    bucket = settings.S3_PUBLIC_BUCKET if public else settings.S3_BUCKET
    if not bucket:
        raise HTTPException(status_code=500, detail="Object storage bucket is not configured")
    client().put_object(
        Bucket=bucket,
        Key=key.lstrip("/"),
        Body=data,
        ContentType=content_type or "application/octet-stream",
    )


def get_bytes(key: str, *, public: bool = False) -> bytes:
    bucket = settings.S3_PUBLIC_BUCKET if public else settings.S3_BUCKET
    if not bucket:
        raise HTTPException(status_code=500, detail="Object storage bucket is not configured")
    try:
        response = client().get_object(Bucket=bucket, Key=key.lstrip("/"))
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in {"NoSuchKey", "404", "NotFound"}:
            raise HTTPException(status_code=404, detail="File not found") from exc
        raise HTTPException(status_code=502, detail="Object storage read failed") from exc
    return response["Body"].read()


def delete_object(key: str, *, public: bool = False) -> None:
    bucket = settings.S3_PUBLIC_BUCKET if public else settings.S3_BUCKET
    if not bucket:
        return
    try:
        client().delete_object(Bucket=bucket, Key=key.lstrip("/"))
    except ClientError:
        logger.warning("Could not delete object %s", key)

