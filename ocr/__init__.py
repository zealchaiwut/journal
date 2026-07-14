"""OCR backend dispatch. Select with OCR_BACKEND env (macos | claude)."""

import os


def get_backend():
    """Return a module exposing ocr_image(png_bytes: bytes) -> str."""
    name = os.environ.get("OCR_BACKEND", "macos").lower()
    if name == "macos":
        from ocr import macos_vision
        return macos_vision
    if name == "claude":
        from ocr import claude_vision
        return claude_vision
    raise ValueError(f"Unknown OCR_BACKEND: {name!r} (expected macos or claude)")
