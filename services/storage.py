from pathlib import Path, PurePosixPath
from typing import Protocol


class Storage(Protocol):
    def save(self, key: str, content: bytes) -> str: ...

    def read(self, key: str) -> bytes: ...

    def delete(self, key: str) -> None: ...


def _validate_key(key: str) -> None:
    path = PurePosixPath(key)
    if (
        not key
        or "\\" in key
        or path.is_absolute()
        or any(part in {".", ".."} for part in path.parts)
    ):
        raise ValueError("Storage keys must be non-empty relative paths.")


class LocalStorage:
    def __init__(self, root: str | Path = "uploads") -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, key: str) -> Path:
        _validate_key(key)
        path = (self.root / Path(*PurePosixPath(key).parts)).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as error:
            raise ValueError("Storage key resolves outside the storage root.") from error
        return path

    def save(self, key: str, content: bytes) -> str:
        path = self._path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return key

    def read(self, key: str) -> bytes:
        return self._path_for(key).read_bytes()

    def delete(self, key: str) -> None:
        path = self._path_for(key)
        path.unlink(missing_ok=True)


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
    ) -> None:
        import boto3
        from botocore.config import Config

        self.bucket = bucket
        client_options: dict[str, str | Config] = {
            "endpoint_url": endpoint_url,
            "region_name": region,
            "config": Config(s3={"addressing_style": "path"}),
        }
        if access_key_id and secret_access_key:
            client_options["aws_access_key_id"] = access_key_id
            client_options["aws_secret_access_key"] = secret_access_key
        self.client = boto3.client("s3", **client_options)

    def save(self, key: str, content: bytes) -> str:
        _validate_key(key)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=content)
        return key

    def read(self, key: str) -> bytes:
        _validate_key(key)
        response = self.client.get_object(Bucket=self.bucket, Key=key)
        with response["Body"] as body:
            return body.read()

    def delete(self, key: str) -> None:
        _validate_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)


def create_storage() -> Storage:
    from backend.app.core.config import get_settings

    settings = get_settings()
    backend = settings.storage_backend.strip().lower()
    if backend == "local":
        return LocalStorage(settings.upload_dir)
    if backend == "s3":
        if not settings.s3_bucket:
            raise RuntimeError("S3_BUCKET must be set when STORAGE_BACKEND=s3.")
        return S3Storage(
            settings.s3_bucket,
            endpoint_url=settings.s3_endpoint_url,
            region=settings.s3_region,
            access_key_id=settings.s3_access_key_id,
            secret_access_key=settings.s3_secret_access_key,
        )
    raise ValueError(
        f"Unsupported STORAGE_BACKEND {backend!r}. Choose 'local' or 's3'."
    )
