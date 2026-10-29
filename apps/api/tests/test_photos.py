from io import BytesIO

import pytest
from PIL import Image, ImageCms, PngImagePlugin

from flagman.core.photos import (
    MAX_PIXELS,
    MAX_UPLOAD_BYTES,
    PhotoTooLargeError,
    PhotoTypeUnsupportedError,
    clean_photo,
)

_EXIF_ORIENTATION = 0x0112
_EXIF_MAKE = 0x010F
_GPS_IFD = 0x8825
_GPS_LATITUDE_REF = 1
_ROTATED_90 = 6


def _photo(size: tuple[int, int] = (40, 20), image_format: str = "JPEG", **params: object) -> bytes:
    output = BytesIO()
    Image.new("RGB", size, (200, 30, 30)).save(output, format=image_format, **params)
    return output.getvalue()


def _exif_with_gps() -> Image.Exif:
    exif = Image.Exif()
    exif[_EXIF_MAKE] = "SecretPhone"
    exif.get_ifd(_GPS_IFD)[_GPS_LATITUDE_REF] = "N"
    return exif


def _open(data: bytes) -> Image.Image:
    image = Image.open(BytesIO(data))
    image.load()
    return image


def test_metadata_is_gone_from_a_jpeg() -> None:
    srgb = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    original = _photo(
        exif=_exif_with_gps(),
        icc_profile=srgb,
        comment=b"taken at home",
        xmp=b"<x:xmpmeta>secret</x:xmpmeta>",
    )

    cleaned = clean_photo(original)

    image = _open(cleaned)
    assert image.format == "JPEG"
    assert dict(image.getexif()) == {}
    assert not {"exif", "icc_profile", "comment", "xmp"} & set(image.info)
    for secret in (b"SecretPhone", b"taken at home", b"xmpmeta"):
        assert secret not in cleaned


def test_the_photo_is_turned_upright_before_the_orientation_is_dropped() -> None:
    exif = Image.Exif()
    exif[_EXIF_ORIENTATION] = _ROTATED_90

    image = _open(clean_photo(_photo((40, 20), exif=exif)))

    assert image.size == (20, 40)


@pytest.mark.parametrize("image_format", ["PNG", "WEBP"])
def test_png_and_webp_become_jpeg(image_format: str) -> None:
    image = _open(clean_photo(_photo(image_format=image_format)))

    assert image.format == "JPEG"
    assert image.size == (40, 20)


def test_png_text_chunks_are_dropped() -> None:
    text = PngImagePlugin.PngInfo()
    text.add_text("Location", "Minsk, home")

    cleaned = clean_photo(_photo(image_format="PNG", pnginfo=text))

    assert b"Minsk" not in cleaned


def test_a_transparent_png_is_accepted() -> None:
    output = BytesIO()
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(output, format="PNG")

    assert _open(clean_photo(output.getvalue())).mode == "RGB"


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"just text, named photo.jpg", id="text"),
        pytest.param(b"%PDF-1.7\n%\xe2\xe3\xcf\xd3\n", id="pdf"),
        pytest.param(_photo(image_format="GIF"), id="gif"),
        pytest.param(_photo(image_format="BMP"), id="bmp"),
        pytest.param(_photo((400, 400))[:300], id="truncated-jpeg"),
    ],
)
def test_anything_but_a_jpeg_png_or_webp_is_refused(data: bytes) -> None:
    with pytest.raises(PhotoTypeUnsupportedError):
        clean_photo(data)


def test_a_file_over_the_size_limit_is_refused() -> None:
    with pytest.raises(PhotoTooLargeError):
        clean_photo(_photo() + b"\0" * MAX_UPLOAD_BYTES)


def test_a_small_file_declaring_too_many_pixels_is_refused_before_decoding() -> None:
    side = int(MAX_PIXELS**0.5) + 1
    output = BytesIO()
    # One bit per pixel and all zeros: a few kilobytes that would decode to tens of megapixels.
    Image.new("1", (side, side)).save(output, format="PNG")
    assert len(output.getvalue()) < MAX_UPLOAD_BYTES

    with pytest.raises(PhotoTooLargeError):
        clean_photo(output.getvalue())
