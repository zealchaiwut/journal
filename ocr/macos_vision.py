"""Handwriting OCR via the macOS Vision framework (local, free).

Languages come from OCR_LANGS env (comma-separated, e.g. "en-US,th-TH").
"""

import os

import Quartz
import Vision
from Foundation import NSData


def ocr_image(png_bytes: bytes) -> str:
    """Recognize text in a PNG image, top-to-bottom reading order."""
    ns_data = NSData.dataWithBytes_length_(png_bytes, len(png_bytes))
    source = Quartz.CGImageSourceCreateWithData(ns_data, None)
    if source is None:
        raise ValueError("Could not decode image data")
    cg_image = Quartz.CGImageSourceCreateImageAtIndex(source, 0, None)
    if cg_image is None:
        raise ValueError("Could not create CGImage from data")

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setUsesLanguageCorrection_(True)
    langs = [
        s.strip()
        for s in os.environ.get("OCR_LANGS", "en-US").split(",")
        if s.strip()
    ]
    request.setRecognitionLanguages_(langs)

    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(
        cg_image, None
    )
    ok, error = handler.performRequests_error_([request], None)
    if not ok:
        raise RuntimeError(f"Vision OCR failed: {error}")

    observations = request.results() or []
    # Vision coordinates are bottom-left origin: sort top-to-bottom, then left-to-right.
    lines = sorted(
        observations,
        key=lambda o: (-o.boundingBox().origin.y, o.boundingBox().origin.x),
    )
    out = []
    for obs in lines:
        candidates = obs.topCandidates_(1)
        if candidates and len(candidates):
            out.append(str(candidates[0].string()))
    return "\n".join(out)
