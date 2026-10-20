"""Guarda archivos en disco y, si DATA_BUCKET está definido, también en S3."""
import os
from pathlib import Path

from src.config import DATA_BUCKET, ROOT


def save(rel_path: str, data: bytes) -> Path:
    local = ROOT / rel_path
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_bytes(data)
    if DATA_BUCKET:
        import boto3  # solo se necesita cuando se corre contra AWS

        boto3.client("s3").put_object(Bucket=DATA_BUCKET, Key=rel_path.replace(os.sep, "/"), Body=data)
    return local
