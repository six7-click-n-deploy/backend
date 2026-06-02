"""Helpers for the App-image data-URL ↔ bytes round-trip.

The API exposes ``apps.image`` as a single ``data:<mime>;base64,<...>``
string: clients can put it straight into an ``<img :src=...>`` and the
write path accepts the same shape, so a "load image, edit, save" round
trip is a noop. Behind the scenes the bytes go to ``apps.image`` and
the mime to ``apps.image_mime`` (see migration ``a7b8c9d0e1f2``).

Two responsibilities live here:

* ``parse_image_data_url`` decodes an incoming data-URL into
  ``(bytes, mime)``, validates size and mime, and raises an
  ``HTTPException`` with a 4xx message that matches the standard
  FastAPI error shape on failure.
* ``build_image_data_url`` does the reverse for the response payload.
"""

from __future__ import annotations

import base64
import re
from typing import Optional

from fastapi import HTTPException, status

# 2 MiB on the decoded byte payload. The base64 representation in
# transit is ~4/3 the size; clients should keep that in mind, but we
# enforce on the decoded length so the limit is meaningful regardless
# of the encoding overhead.
MAX_IMAGE_BYTES = 2 * 1024 * 1024

# Permissive on the mime side — anything an HTML5 ``<img>`` can render
# is fair game. The frontend already restricts the file picker to
# ``image/*`` and we trust the data-URL prefix; if a client lies we
# reject only the obviously-not-an-image case.
_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>image/[a-zA-Z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=\s]+)$"
)


def parse_image_data_url(data_url: Optional[str]) -> tuple[Optional[bytes], Optional[str]]:
    """Decode a data-URL into ``(bytes, mime)``.

    Returns ``(None, None)`` for ``None`` or empty string — the empty
    string is a useful sentinel from the update endpoint meaning
    "clear the image". Otherwise raises 422 if the input doesn't
    parse, or 413 if the decoded payload is larger than ``MAX_IMAGE_BYTES``.
    """
    if data_url is None or data_url == "":
        return None, None
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "reason": "invalid_image_format",
                "message": (
                    "image must be a data-URL like "
                    "'data:image/png;base64,<...>'"
                ),
            },
        )
    mime = match.group("mime").lower()
    try:
        payload = base64.b64decode(match.group("payload"), validate=True)
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"reason": "invalid_base64", "message": "image payload is not valid base64"},
        )
    if len(payload) > MAX_IMAGE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={
                "reason": "image_too_large",
                "max_bytes": MAX_IMAGE_BYTES,
                "actual_bytes": len(payload),
            },
        )
    return payload, mime


def build_image_data_url(image_bytes: Optional[bytes], image_mime: Optional[str]) -> Optional[str]:
    """Build a data-URL for the API response.

    Returns ``None`` if either side is missing — that's the empty
    state and the schema's ``Optional[str]`` lets it pass through as
    JSON ``null``. The mime is trusted from the DB (it was validated
    at write time); we don't re-validate on read.
    """
    if not image_bytes or not image_mime:
        return None
    encoded = base64.b64encode(image_bytes).decode("ascii")
    return f"data:{image_mime};base64,{encoded}"
