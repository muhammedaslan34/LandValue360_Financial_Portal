from __future__ import annotations

import os
import sys
import uuid

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

S3_CLIENT_CONFIG = Config(signature_version="s3v4", s3={"addressing_style": "path"})


def main() -> int:
    endpoint = os.environ.get("LV360_PORTAL_S3_ENDPOINT_URL", "")
    bucket = os.environ.get("LV360_PORTAL_S3_BUCKET", "")
    region = os.environ.get("LV360_PORTAL_S3_REGION", "eu-central-2")
    key_id = os.environ.get("LV360_PORTAL_S3_ACCESS_KEY", "")
    secret = os.environ.get("LV360_PORTAL_S3_SECRET_KEY", "")
    if not all([endpoint, bucket, key_id, secret]):
        print("Missing S3 environment variables")
        return 1

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=key_id,
        aws_secret_access_key=secret,
        config=S3_CLIENT_CONFIG,
    )

    object_key = f"healthchecks/{uuid.uuid4().hex}.txt"
    payload = b"LandValue360 iDrive E2 upload test\n"
    try:
        client.put_object(Bucket=bucket, Key=object_key, Body=payload, ContentType="text/plain")
        print(f"UPLOAD OK: s3://{bucket}/{object_key}")
        body = client.get_object(Bucket=bucket, Key=object_key)["Body"].read()
        if body != payload:
            print("VERIFY FAILED: downloaded bytes mismatch")
            return 1
        print("DOWNLOAD OK: content verified")
        client.delete_object(Bucket=bucket, Key=object_key)
        print("DELETE OK: test object removed")
    except (BotoCoreError, ClientError) as exc:
        print(f"UPLOAD FAILED: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
