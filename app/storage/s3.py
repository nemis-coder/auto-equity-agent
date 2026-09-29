from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.storage.interfaces import StorageError


class S3DocumentStore:
    """Bucket privado S3 compatible (MinIO en local). Verifica SHA-256 al escribir y leer."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        namespace: str = "",
    ) -> None:
        self._bucket = bucket
        # Separa entornos que comparten bucket (demo, pruebas, evaluación): la clave lógica que
        # guarda PostgreSQL no cambia; solo la física lleva el prefijo del entorno.
        self._ns = namespace
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2, "mode": "standard"},
            ),
        )

    async def _call(self, fn, /, **kwargs) -> Any:
        try:
            return await asyncio.to_thread(fn, **kwargs)
        except (BotoCoreError, ClientError) as exc:
            raise StorageError(type(exc).__name__) from exc

    async def put(self, key: str, content: bytes, sha256: str) -> None:
        if hashlib.sha256(content).hexdigest() != sha256:
            raise StorageError("SHA256_MISMATCH_BEFORE_PUT")
        await self._call(
            self._client.put_object,
            Bucket=self._bucket,
            Key=self._ns + key,
            Body=content,
            Metadata={"sha256": sha256},
        )
        head = await self._call(self._client.head_object, Bucket=self._bucket, Key=self._ns + key)
        if head.get("Metadata", {}).get("sha256") != sha256 or head["ContentLength"] != len(
            content
        ):
            raise StorageError("WRITE_NOT_VERIFIED")

    async def get(self, key: str, sha256: str) -> bytes:
        obj = await self._call(self._client.get_object, Bucket=self._bucket, Key=self._ns + key)
        body = await asyncio.to_thread(obj["Body"].read)
        if hashlib.sha256(body).hexdigest() != sha256:
            raise StorageError("SHA256_MISMATCH_ON_READ")
        return body

    async def delete(self, key: str) -> None:
        await self._call(self._client.delete_object, Bucket=self._bucket, Key=self._ns + key)

    async def list_objects(self, prefix: str) -> list[tuple[str, datetime]]:
        """Claves y fecha de escritura bajo un prefijo (para reconciliar huérfanos)."""
        out: list[tuple[str, datetime]] = []
        token = None
        while True:
            kwargs = {"Bucket": self._bucket, "Prefix": self._ns + prefix, "MaxKeys": 1000}
            if token:
                kwargs["ContinuationToken"] = token
            page = await self._call(self._client.list_objects_v2, **kwargs)
            out += [
                (o["Key"].removeprefix(self._ns), o["LastModified"])
                for o in page.get("Contents", [])
            ]
            if not page.get("IsTruncated"):
                return out
            token = page["NextContinuationToken"]

    async def check_ready(self) -> None:
        await self._call(self._client.head_bucket, Bucket=self._bucket)
