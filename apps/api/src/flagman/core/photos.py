"""Uploaded photos: read the raw body within a limit and turn it into a clean JPEG.

Shared by KYC documents and car inspections, see docs/adr/0012. Each module turns the errors
here into problems of its own, so the clients keep the error types they already know.
"""

from io import BytesIO
from typing import Any

from fastapi import Request
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# A 48 MP phone camera saves 12 MP photos by default; 30 MP leaves room and still keeps a decoded
# photo around 90 MB. A small file may declare a huge size, so it is checked before decoding.
MAX_PIXELS = 30_000_000
ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
OUTPUT_CONTENT_TYPE = "image/jpeg"
JPEG_QUALITY = 90

# Decided by the bytes, not by the file name or the Content-Type the client sent.
_ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP")


class PhotoTooLargeError(Exception):
    """The message, if any, is safe to send to the client."""


class PhotoTypeUnsupportedError(Exception):
    pass


def photo_request_body(what: str) -> dict[str, Any]:
    """`openapi_extra` of an endpoint that takes a photo as the raw request body.

    The raw body and not a multipart form: Starlette writes a multipart file to a temporary file
    in full before the size limit could stop it.
    """
    return {
        "requestBody": {
            "required": True,
            "description": (
                f"{what}, at most {MAX_UPLOAD_BYTES // (1024 * 1024)} MB. "
                "The type is detected from the content; the stored copy is a JPEG without metadata."
            ),
            "content": {
                content_type: {"schema": {"type": "string", "format": "binary"}}
                for content_type in ACCEPTED_CONTENT_TYPES
            },
        }
    }


async def read_photo_body(request: Request) -> bytes:
    """Raises PhotoTooLargeError as soon as the body is over the limit."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_UPLOAD_BYTES:
        raise PhotoTooLargeError
    body = bytearray()
    # Chunked uploads have no length up front, so the limit is also enforced while reading.
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_UPLOAD_BYTES:
            raise PhotoTooLargeError
    return bytes(body)


def clean_photo(data: bytes) -> bytes:
    """Re-encodes the photo as JPEG without any metadata (EXIF, GPS, XMP, ICC, comments).

    Re-encoding instead of deleting known tags: whatever the original file carried, the output
    holds only pixels. CPU-bound: callers run it in a worker thread, not on the event loop.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoTooLargeError
    try:
        with Image.open(BytesIO(data), formats=_ACCEPTED_FORMATS) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise PhotoTooLargeError("The photo has too many pixels.")
            # The orientation lives in EXIF, which is dropped: turn the pixels upright first.
            upright = ImageOps.exif_transpose(image)
            clean = upright.convert("RGB")
    except (
        UnidentifiedImageError,
        Image.DecompressionBombError,
        OSError,
        SyntaxError,
        ValueError,
    ) as error:
        # Truncated or malformed files fail while decoding with any of these.
        raise PhotoTypeUnsupportedError from error
    # convert() copies the metadata dict, and save() would write parts of it back.
    clean.info = {}
    output = BytesIO()
    clean.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()
