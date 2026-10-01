"""CAPTCHA OCR wrapper around ddddocr with light cleanup.

ddddocr is a pure-pip OCR model tuned for exactly this style of short
alphanumeric captcha, so there is no system Tesseract dependency.

Tried upscaling the (tiny, ~109x66px) captcha before recognition on the
theory that more pixels would help the model separate close characters -
measured live against the real portal (backend/captcha.py history), it made
accuracy *worse* (33% vs a 58% baseline over matched 12-attempt samples), so
don't reintroduce that without re-measuring. ddddocr's model appears tuned
to roughly this input size already.
"""
from __future__ import annotations

import re
import threading

_ocr = None
_ocr_lock = threading.Lock()


def _engine():
    global _ocr
    if _ocr is None:
        with _ocr_lock:
            if _ocr is None:
                import ddddocr  # imported lazily so the web app starts without it

                _ocr = ddddocr.DdddOcr(show_ad=False)
    return _ocr


def recognise(image_bytes: bytes) -> str:
    """Return the OCR's best guess, upper-cased and stripped of non-alphanumerics."""
    try:
        raw = _engine().classification(image_bytes)
    except Exception:
        return ""
    return clean(raw)


def clean(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", text or "").upper()


def looks_plausible(text: str, expected_len: int | None = None) -> bool:
    if not text or not text.isalnum():
        return False
    if expected_len is not None:
        return len(text) == expected_len
    return 4 <= len(text) <= 8
