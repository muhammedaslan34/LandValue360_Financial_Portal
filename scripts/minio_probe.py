from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def main() -> int:
    endpoint = os.environ.get("LV360_PORTAL_S3_ENDPOINT_URL", "http://minio:9000")
    public = os.environ.get("LV360_PORTAL_S3_PUBLIC_ENDPOINT_URL", "")
    bucket = os.environ.get("LV360_PORTAL_S3_BUCKET", "lv360-private")
    key = os.environ.get("LV360_PORTAL_S3_ACCESS_KEY")
    secret = os.environ.get("LV360_PORTAL_S3_SECRET_KEY")
    if not key or not secret:
        print("Missing S3 credentials in environment")
        return 1

    client = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=os.environ.get("LV360_PORTAL_S3_REGION", "us-east-1"),
        aws_access_key_id=key,
        aws_secret_access_key=secret,
    )
    try:
        buckets = [item["Name"] for item in client.list_buckets().get("Buckets", [])]
        print(f"INTERNAL OK: endpoint={endpoint} buckets={buckets}")
        client.head_bucket(Bucket=bucket)
        print(f"BUCKET OK: {bucket}")
    except (BotoCoreError, ClientError) as exc:
        print(f"INTERNAL FAILED: {exc}")
        return 1

    if public:
        public_client = boto3.client(
            "s3",
            endpoint_url=public,
            region_name=os.environ.get("LV360_PORTAL_S3_REGION", "us-east-1"),
            aws_access_key_id=key,
            aws_secret_access_key=secret,
        )
        try:
            public_client.list_buckets()
            print(f"PUBLIC OK: endpoint={public}")
        except (BotoCoreError, ClientError) as exc:
            print(f"PUBLIC FAILED: endpoint={public} error={exc}")
            return 1
    else:
        print("PUBLIC SKIPPED: LV360_PORTAL_S3_PUBLIC_ENDPOINT_URL is empty")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
