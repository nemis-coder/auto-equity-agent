"""Crea el bucket privado y el usuario de aplicación de MinIO con permisos mínimos.

Se ejecuta como job `minio-init`, el único proceso (además de MinIO) con credenciales root.
Es idempotente.
"""

from __future__ import annotations

import json
import os
import sys
import time
from urllib.parse import urlparse

from minio import Minio
from minio.credentials import StaticProvider
from minio.error import MinioAdminException, S3Error
from minio.minioadmin import MinioAdmin

POLICY_NAME = "auto-equity-api-rw"


def bucket_policy(bucket: str) -> dict:
    return {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:GetBucketLocation", "s3:ListBucket"],
                "Resource": [f"arn:aws:s3:::{bucket}"],
            },
            {
                "Effect": "Allow",
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket}/*"],
            },
        ],
    }


def main() -> int:
    parsed = urlparse(os.environ["S3_ENDPOINT_URL"])
    endpoint, secure = parsed.netloc, parsed.scheme == "https"
    root = StaticProvider(os.environ["MINIO_ROOT_USER"], os.environ["MINIO_ROOT_PASSWORD"])
    bucket = os.environ["S3_BUCKET"]
    app_key = os.environ["S3_ACCESS_KEY_ID"]
    app_secret = os.environ["S3_SECRET_ACCESS_KEY"]

    client = Minio(
        endpoint,
        access_key=os.environ["MINIO_ROOT_USER"],
        secret_key=os.environ["MINIO_ROOT_PASSWORD"],
        secure=secure,
        region=os.environ.get("S3_REGION", "us-east-1"),
    )
    for attempt in range(30):
        try:
            client.list_buckets()
            break
        except Exception:  # noqa: BLE001 - espera activa acotada al arranque de MinIO
            if attempt == 29:
                raise
            time.sleep(1)

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
    try:
        client.delete_bucket_policy(bucket)
    except S3Error:
        pass

    admin = MinioAdmin(endpoint=endpoint, credentials=root, secure=secure)
    admin.policy_add(POLICY_NAME, policy=bucket_policy(bucket))
    admin.user_add(app_key, app_secret)
    try:
        admin.attach_policy([POLICY_NAME], user=app_key)
    except MinioAdminException as exc:
        if "already" not in str(exc).lower():
            raise
    info = json.loads(admin.user_info(app_key))
    policy = info.get("policyName")
    print(f"Bucket privado '{bucket}' y usuario '{app_key}' listos (policy: {policy}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
