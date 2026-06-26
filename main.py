import sys
from pathlib import Path

from decode import decode
from encode import encode, _IMAGE_FMT, _VIDEO_FMT

BUNDLE_PATH = "output/Presentation.probundle"
IN_DIR = "in"
OUT_DIR = "output"
PRESENTATION_NAME = "Presentation"

_SUPPORTED_EXTS = set(_IMAGE_FMT) | set(_VIDEO_FMT)


def verify_roundtrip(in_dir: str, out_dir: str) -> None:
    """
    Byte-match every source media file against its extracted counterpart.
    Exits non-zero on any missing file or byte mismatch.
    """
    assets_dir = Path(out_dir) / "assets" / "Media" / "Assets"
    src_files = sorted(
        f for f in Path(in_dir).iterdir()
        if f.is_file() and f.suffix.lower() in _SUPPORTED_EXTS
    )

    missing = []
    mismatches = []

    for src in src_files:
        extracted = assets_dir / src.name
        if not extracted.exists():
            missing.append(src.name)
        elif src.read_bytes() != extracted.read_bytes():
            mismatches.append(src.name)

    if missing:
        print(f"MISSING from bundle: {missing}", file=sys.stderr)
    if mismatches:
        print(f"BYTE MISMATCH detected: {mismatches}", file=sys.stderr)

    if missing or mismatches:
        sys.exit(1)

    print(f"Integrity OK — {len(src_files)} files verified byte-for-byte.")


def main() -> None:
    print("=== ENCODE ===")
    encode(BUNDLE_PATH, IN_DIR, PRESENTATION_NAME)

    print("\n=== DECODE ===")
    decode(BUNDLE_PATH, OUT_DIR)

    print("\n=== VERIFY ===")
    verify_roundtrip(IN_DIR, OUT_DIR)


if __name__ == "__main__":
    main()
