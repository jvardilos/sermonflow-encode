"""
Encode → decode → byte-match integrity tests.

These are the core corruption checks: every asset that goes in must come out
bit-for-bit identical. A failure here means either the ZIP packing, the
custom LFH scanner, or the chunked copy has a bug.
"""
import json
import os
from pathlib import Path

import pytest

from decode import decode, _scan_zip_entries
from encode import encode
from tests.helpers import make_tiff, make_jpeg, make_mov, make_png


def _assert_no_corruption(in_dir: Path, out_dir: Path) -> None:
    """Byte-match every file in in_dir against its counterpart in extracted assets."""
    assets_dir = out_dir / "assets" / "Media" / "Assets"
    src_files = sorted(f for f in in_dir.iterdir() if f.is_file())
    assert src_files, "in_dir is empty — test is misconfigured"

    for src in src_files:
        extracted = assets_dir / src.name
        assert extracted.exists(), f"{src.name} not found in extracted assets"
        assert src.read_bytes() == extracted.read_bytes(), (
            f"{src.name}: byte mismatch after encode→decode round-trip"
        )


# ── core byte-match tests ─────────────────────────────────────────────────────

def test_roundtrip_images_byte_perfect(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "slide1.tif").write_bytes(make_tiff(1920, 1080))
    (in_dir / "slide2.jpg").write_bytes(make_jpeg(1280, 720))
    (in_dir / "slide3.png").write_bytes(make_png(800, 600))

    encode(str(tmp_path / "out.probundle"), str(in_dir), "RoundtripTest")
    decode(str(tmp_path / "out.probundle"), str(tmp_path / "out"), extract=True)

    _assert_no_corruption(in_dir, tmp_path / "out")


def test_roundtrip_video_byte_perfect(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    video = make_mov(ts=600, dur=3000, prores=True)
    (in_dir / "clip.mov").write_bytes(video)

    encode(str(tmp_path / "out.probundle"), str(in_dir), "VideoTest")
    decode(str(tmp_path / "out.probundle"), str(tmp_path / "out"), extract=True)

    _assert_no_corruption(in_dir, tmp_path / "out")


def test_roundtrip_mixed_media_byte_perfect(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "a_image.tif").write_bytes(make_tiff())
    (in_dir / "b_video.mov").write_bytes(make_mov())
    (in_dir / "c_image.jpg").write_bytes(make_jpeg())

    encode(str(tmp_path / "out.probundle"), str(in_dir), "MixedTest")
    decode(str(tmp_path / "out.probundle"), str(tmp_path / "out"), extract=True)

    _assert_no_corruption(in_dir, tmp_path / "out")


def test_roundtrip_larger_file_no_corruption(tmp_path):
    """1 MB payload — exercises the chunked copy path in _copy_entry."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    # Deterministic 1 MB: byte values cycle 0-255 so any truncation is visible
    large_payload = make_tiff() + bytes(i % 256 for i in range(1024 * 1024))
    (in_dir / "large.tif").write_bytes(large_payload)

    encode(str(tmp_path / "out.probundle"), str(in_dir), "LargeTest")
    decode(str(tmp_path / "out.probundle"), str(tmp_path / "out"), extract=True)

    extracted = tmp_path / "out" / "assets" / "Media" / "Assets" / "large.tif"
    assert extracted.read_bytes() == large_payload


# ── structural integrity tests ────────────────────────────────────────────────

def test_roundtrip_slide_count_matches_input_files(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "a.tif").write_bytes(make_tiff())
    (in_dir / "b.tif").write_bytes(make_tiff())
    (in_dir / "c.jpg").write_bytes(make_jpeg())

    encode(str(tmp_path / "out.probundle"), str(in_dir), "CountTest")
    decode(str(tmp_path / "out.probundle"), str(tmp_path / "out"), extract=False)

    with open(tmp_path / "out" / "presentation.json") as f:
        data = json.load(f)
    assert len(data.get("cues", [])) == 3


def test_roundtrip_assets_are_ordered_alphabetically_in_bundle(tmp_path):
    """stat_dir_entries sorts by name; _write_bundle must preserve that order."""
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "c_slide.tif").write_bytes(make_tiff())
    (in_dir / "a_slide.jpg").write_bytes(make_jpeg())
    (in_dir / "b_slide.tif").write_bytes(make_tiff())

    bundle = str(tmp_path / "out.probundle")
    encode(bundle, str(in_dir), "OrderTest")

    asset_names = [
        Path(e[0]).name
        for e in _scan_zip_entries(bundle)
        if not e[0].endswith(".pro")
    ]
    assert asset_names == sorted(asset_names, key=str.lower)


def test_roundtrip_manifest_asset_count_matches_input(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "s1.tif").write_bytes(make_tiff())
    (in_dir / "s2.jpg").write_bytes(make_jpeg())

    bundle = str(tmp_path / "out.probundle")
    encode(bundle, str(in_dir), "ManifestTest")
    decode(bundle, str(tmp_path / "out"), extract=True)

    with open(tmp_path / "out" / "manifest.json") as f:
        manifest = json.load(f)
    assert manifest["asset_summary"]["total_count"] == 2


def test_roundtrip_presentation_name_survives_encode_decode(tmp_path):
    in_dir = tmp_path / "in"
    in_dir.mkdir()
    (in_dir / "slide.tif").write_bytes(make_tiff())

    bundle = str(tmp_path / "out.probundle")
    encode(bundle, str(in_dir), "SpecificName")
    decode(bundle, str(tmp_path / "out"), extract=False)

    with open(tmp_path / "out" / "presentation.json") as f:
        data = json.load(f)
    assert data.get("name") == "SpecificName"
