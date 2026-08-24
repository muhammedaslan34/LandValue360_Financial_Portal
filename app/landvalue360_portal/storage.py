from __future__ import annotations

import hashlib
import mimetypes
import secrets
import io
import zipfile
from pathlib import Path

import boto3
from botocore.config import Config

from .config import get_settings

S3_CLIENT_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})

ALLOWED_EXTENSIONS = {".pdf", ".docx", ".xlsx", ".jpg", ".jpeg", ".png"}
ALLOWED_MIME = {
    ".pdf": {"application/pdf"},
    ".docx": {"application/vnd.openxmlformats-officedocument.wordprocessingml.document", "application/zip"},
    ".xlsx": {"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "application/zip"},
    ".jpg": {"image/jpeg"},
    ".jpeg": {"image/jpeg"},
    ".png": {"image/png"},
}


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_upload(filename: str, content_type: str | None, data: bytes, *, max_bytes: int, allowed_mime: set[str] | None = None) -> tuple[str, str]:
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file extension")
    if len(data) > max_bytes:
        raise ValueError("File exceeds the permitted size")
    mime = (content_type or mimetypes.guess_type(filename)[0] or "application/octet-stream").lower()
    permitted_mime = allowed_mime if allowed_mime is not None else ALLOWED_MIME[suffix]
    if mime not in permitted_mime:
        raise ValueError("File MIME type does not match the permitted type")
    # Lightweight signature checks. DOCX/XLSX are ZIP containers.
    signatures = {
        ".pdf": b"%PDF-",
        ".jpg": b"\xff\xd8\xff",
        ".jpeg": b"\xff\xd8\xff",
        ".png": b"\x89PNG\r\n\x1a\n",
        ".docx": b"PK\x03\x04",
        ".xlsx": b"PK\x03\x04",
    }
    if not data.startswith(signatures[suffix]):
        raise ValueError("File signature is invalid")
    if suffix in {".docx", ".xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
                if len(names) > 5000:
                    raise ValueError("Office document contains too many entries")
                uncompressed = sum(info.file_size for info in archive.infolist())
                if uncompressed > max_bytes * 20:
                    raise ValueError("Office document expands beyond the permitted limit")
                required = {"[Content_Types].xml"}
                if suffix == ".docx":
                    required.add("word/document.xml")
                else:
                    required.add("xl/workbook.xml")
                if not required.issubset(names):
                    raise ValueError("Office document structure is invalid")
        except zipfile.BadZipFile as exc:
            raise ValueError("Office document container is invalid") from exc
    return suffix, mime


class Storage:
    def put(self, *, project_id: str, data: bytes, suffix: str) -> str:
        raise NotImplementedError

    def get(self, key: str) -> bytes:
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    def signed_url(self, key: str, expires_seconds: int = 300) -> str | None:
        return None


class LocalStorage(Storage):
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, *, project_id: str, data: bytes, suffix: str) -> str:
        key = f"projects/{project_id}/{secrets.token_hex(24)}{suffix}"
        target = (self.root / key).resolve()
        if self.root not in target.parents:
            raise ValueError("Unsafe storage key")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        target = (self.root / key).resolve()
        if self.root not in target.parents or not target.exists():
            raise FileNotFoundError(key)
        return target.read_bytes()

    def delete(self, key: str) -> None:
        target = (self.root / key).resolve()
        if self.root in target.parents and target.exists():
            target.unlink()


class S3Storage(Storage):
    def __init__(self):
        settings = get_settings()
        self.bucket = settings.s3_bucket
        self.public_endpoint = settings.s3_public_endpoint_url
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            config=S3_CLIENT_CONFIG,
        )

    def put(self, *, project_id: str, data: bytes, suffix: str) -> str:
        key = f"projects/{project_id}/{secrets.token_hex(24)}{suffix}"
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)
        return key

    def get(self, key: str) -> bytes:
        return self.client.get_object(Bucket=self.bucket, Key=key)["Body"].read()

    def delete(self, key: str) -> None:
        self.client.delete_object(Bucket=self.bucket, Key=key)

    def signed_url(self, key: str, expires_seconds: int = 300) -> str | None:
        if not self.public_endpoint:
            return None
        public_client = boto3.client(
            "s3",
            endpoint_url=self.public_endpoint,
            region_name=get_settings().s3_region,
            aws_access_key_id=get_settings().s3_access_key,
            aws_secret_access_key=get_settings().s3_secret_key,
            config=S3_CLIENT_CONFIG,
        )
        return public_client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=expires_seconds,
        )


def get_storage() -> Storage:
    settings = get_settings()
    if settings.storage_backend.lower() == "s3":
        return S3Storage()
    return LocalStorage(settings.storage_path)
