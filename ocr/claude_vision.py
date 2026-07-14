"""Claude API vision OCR backend — not implemented yet.

TODO: when macOS Vision quality disappoints, implement with the anthropic SDK:
send each page PNG as an image block, prompt for a verbatim transcription.
Needs ANTHROPIC_API_KEY in .env and `anthropic` in requirements.txt.
"""


def ocr_image(png_bytes: bytes) -> str:
    raise NotImplementedError(
        "Claude vision OCR backend not implemented yet — use OCR_BACKEND=macos"
    )
