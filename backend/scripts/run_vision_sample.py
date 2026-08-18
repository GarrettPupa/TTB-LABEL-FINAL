"""Run one live label extraction without storing the processed image."""

import argparse
import os
from pathlib import Path

from backend.app.vision import (
    OpenAIVisionService,
    VisionServiceError,
    VisionTimeoutError,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Extract typed label fields from one local sample image."
    )
    parser.add_argument("image", type=Path, help="Path to a PNG, JPEG, or WebP label")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not os.getenv("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not set; no API request was made.")
        return 2
    if not args.image.is_file():
        print(f"Sample image does not exist: {args.image}")
        return 2

    try:
        extracted = OpenAIVisionService().extract_label(args.image.read_bytes())
    except VisionTimeoutError:
        print("Label analysis timed out. Please try again.")
        return 2
    except VisionServiceError:
        print("The label could not be analyzed. Please try again.")
        return 2
    print(extracted.model_dump_json(indent=2))
    populated = any(
        value is not None for value in extracted.model_dump(mode="python").values()
    )
    return 0 if populated else 1


if __name__ == "__main__":
    raise SystemExit(main())
