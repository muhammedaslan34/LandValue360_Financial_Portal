"""Bounded request-body readers for upload endpoints.

FastAPI's ``bytes = Body(...)`` materializes the complete request before an
endpoint can enforce its configured limit.  These helpers reject oversized
uploads while streaming, preventing a project package or evidence document
from consuming unbounded memory.
"""
from __future__ import annotations

from fastapi import Request

from .errors import ConflictError


async def read_limited_body(
    request: Request,
    *,
    max_bytes: int,
    error_code: str,
    error_message: str,
) -> bytes:
    declared = request.headers.get("content-length")
    if declared:
        try:
            if int(declared) > max_bytes:
                raise ConflictError(error_code, error_message)
        except ValueError:
            raise ConflictError("INVALID_CONTENT_LENGTH", "Content-Length must be an integer.") from None
    payload = bytearray()
    async for chunk in request.stream():
        if not chunk:
            continue
        if len(payload) + len(chunk) > max_bytes:
            raise ConflictError(error_code, error_message)
        payload.extend(chunk)
    return bytes(payload)
