"""A test photo built at run time, so no binary lives in the repository."""

from io import BytesIO

from PIL import Image

# What the app uploads: the long side and JPEG quality of apps/mobile/src/features/kyc/photos.ts.
_SIZE = (2560, 1920)
_JPEG_QUALITY = 85
# Enough noise to land near the megabyte of a real phone photo; a flat image would hide the cost of
# storing and decoding one.
_NOISE_SIGMA = 12


def make_photo() -> bytes:
    noise = Image.effect_noise(_SIZE, _NOISE_SIGMA)
    gradient = Image.linear_gradient("L").resize(_SIZE)
    image = Image.merge("RGB", (noise, gradient, noise))
    output = BytesIO()
    image.save(output, format="JPEG", quality=_JPEG_QUALITY)
    return output.getvalue()
