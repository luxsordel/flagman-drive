"""Turns an uploaded document photo into a clean JPEG, see docs/adr/0012. No I/O here.

CPU-bound: callers run it in a worker thread, not on the event loop.
"""

from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from flagman.kyc.domain import DocumentTooLargeError, DocumentTypeUnsupportedError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# A 48 MP phone camera saves 12 MP photos by default; 30 MP leaves room and still keeps a decoded
# photo around 90 MB. A small file may declare a huge size, so it is checked before decoding.
MAX_PIXELS = 30_000_000
ACCEPTED_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
OUTPUT_CONTENT_TYPE = "image/jpeg"
JPEG_QUALITY = 90

# Decided by the bytes, not by the file name or the Content-Type the client sent.
_ACCEPTED_FORMATS = ("JPEG", "PNG", "WEBP")


def clean_document_photo(data: bytes) -> bytes:
    """Re-encodes the photo as JPEG without any metadata (EXIF, GPS, XMP, ICC, comments).

    Re-encoding instead of deleting known tags: whatever the original file carried, the output
    holds only pixels.
    """
    if len(data) > MAX_UPLOAD_BYTES:
        raise DocumentTooLargeError
    try:
        with Image.open(BytesIO(data), formats=_ACCEPTED_FORMATS) as image:
            width, height = image.size
            if width * height > MAX_PIXELS:
                raise DocumentTooLargeError("The photo has too many pixels.")
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
        raise DocumentTypeUnsupportedError from error
    # convert() copies the metadata dict, and save() would write parts of it back.
    clean.info = {}
    output = BytesIO()
    clean.save(output, format="JPEG", quality=JPEG_QUALITY, optimize=True)
    return output.getvalue()
