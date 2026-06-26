import json
import os
import zipfile
import pytest
from pathlib import Path

from decode import (
    _scan_zip_entries,
    _copy_entry,
    _normalize_zip_path,
    extract_bundle,
    build_manifest,
    decode,
)
from tests.helpers import make_tiff, make_jpeg, make_probundle


# ── _normalize_zip_path ───────────────────────────────────────────────────────

class TestNormalizeZipPath:
    def test_keeps_media_assets_path_intact(self):
        assert _normalize_zip_path("Media/Assets/foo.tif") == "Media/Assets/foo.tif"

    def test_strips_absolute_macos_prefix(self):
        result = _normalize_zip_path("/Users/mac/Library/Media/Assets/foo.tif")
        assert result == "Media/Assets/foo.tif"

    def test_bare_filename_unchanged(self):
        assert _normalize_zip_path("Presentation.pro") == "Presentation.pro"

    def test_path_without_media_segment_returns_basename(self):
        result = _normalize_zip_path("/some/random/path/file.pro")
        assert result == "file.pro"

    def test_leading_slash_stripped(self):
        result = _normalize_zip_path("/Media/Assets/img.tif")
        assert result == "Media/Assets/img.tif"


# ── _scan_zip_entries ─────────────────────────────────────────────────────────

class TestScanZipEntries:
    def test_finds_all_entries(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({
            "Pres.pro": b"\x00" * 64,
            "Media/Assets/img.tif": make_tiff(),
        }))
        names = [e[0] for e in _scan_zip_entries(str(bundle))]
        assert "Pres.pro" in names
        assert "Media/Assets/img.tif" in names

    def test_yields_correct_data_size(self, tmp_path):
        content = b"\xAB" * 512
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"file.dat": content}))
        entries = list(_scan_zip_entries(str(bundle)))
        assert len(entries) == 1
        _, _, size = entries[0]
        assert size == 512

    def test_yields_filename_as_first_element(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"hello.pro": b"\x00"}))
        name, _, _ = list(_scan_zip_entries(str(bundle)))[0]
        assert name == "hello.pro"

    def test_multiple_files_all_found(self, tmp_path):
        files = {f"file_{i}.tif": make_tiff() for i in range(5)}
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle(files))
        names = [e[0] for e in _scan_zip_entries(str(bundle))]
        assert len(names) == 5


# ── _copy_entry ───────────────────────────────────────────────────────────────

class TestCopyEntry:
    def test_copies_exact_bytes(self, tmp_path):
        payload = b"exact bytes content here"
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"data.bin": payload}))
        _, offset, size = list(_scan_zip_entries(str(bundle)))[0]
        dest = str(tmp_path / "out" / "data.bin")
        _copy_entry(str(bundle), offset, size, dest)
        assert open(dest, "rb").read() == payload

    def test_creates_parent_directories(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"f.bin": b"\x00" * 8}))
        _, offset, size = list(_scan_zip_entries(str(bundle)))[0]
        dest = str(tmp_path / "a" / "b" / "c" / "f.bin")
        _copy_entry(str(bundle), offset, size, dest)
        assert os.path.exists(dest)

    def test_larger_payload_intact(self, tmp_path):
        payload = bytes(i % 256 for i in range(10_000))
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"big.bin": payload}))
        _, offset, size = list(_scan_zip_entries(str(bundle)))[0]
        dest = str(tmp_path / "big.bin")
        _copy_entry(str(bundle), offset, size, dest)
        assert open(dest, "rb").read() == payload


# ── extract_bundle ────────────────────────────────────────────────────────────

class TestExtractBundle:
    def test_extracts_all_files(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({
            "Pres.pro": b"\x00" * 64,
            "Media/Assets/img.tif": make_tiff(),
        }))
        results = extract_bundle(str(bundle), str(tmp_path / "out"))
        assert len(results) == 2

    def test_skip_extensions_filters_out_files(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({
            "Pres.pro": b"\x00" * 64,
            "Media/Assets/clip.mov": b"\x00" * 128,
        }))
        results = extract_bundle(str(bundle), str(tmp_path / "out"), skip_extensions={".mov"})
        names = [r["filename"] for r in results]
        assert "clip.mov" not in names
        assert "Pres.pro" in names

    def test_returns_correct_size_bytes(self, tmp_path):
        content = b"\xff" * 256
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"Media/Assets/f.tif": content}))
        results = extract_bundle(str(bundle), str(tmp_path / "out"))
        assert results[0]["size_bytes"] == 256

    def test_extracted_file_bytes_match_original(self, tmp_path):
        img = make_tiff(640, 480)
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"Media/Assets/img.tif": img}))
        out = tmp_path / "out"
        extract_bundle(str(bundle), str(out))
        extracted = out / "Media" / "Assets" / "img.tif"
        assert extracted.read_bytes() == img

    def test_creates_output_directory(self, tmp_path):
        bundle = tmp_path / "test.probundle"
        bundle.write_bytes(make_probundle({"f.pro": b"\x00"}))
        out = tmp_path / "new_dir"
        assert not out.exists()
        extract_bundle(str(bundle), str(out))
        assert out.exists()


# ── build_manifest ────────────────────────────────────────────────────────────

class TestBuildManifest:
    def test_has_required_top_level_keys(self, encoded_bundle):
        bundle_path, _ = encoded_bundle
        pres = _load_pres_from_bundle(bundle_path)
        manifest = build_manifest(bundle_path, pres, [])
        for key in ("bundle_file", "extracted_at", "presentation", "assets", "asset_summary", "cue_groups"):
            assert key in manifest

    def test_presentation_name_correct(self, encoded_bundle):
        bundle_path, _ = encoded_bundle
        pres = _load_pres_from_bundle(bundle_path)
        manifest = build_manifest(bundle_path, pres, [])
        assert manifest["presentation"]["name"] == "TestPresentation"

    def test_cue_count_in_manifest(self, encoded_bundle):
        bundle_path, source_dir = encoded_bundle
        pres = _load_pres_from_bundle(bundle_path)
        manifest = build_manifest(bundle_path, pres, [])
        expected = len(list(source_dir.iterdir()))
        assert manifest["presentation"]["cue_count"] == expected

    def test_asset_summary_totals_assets_list(self, encoded_bundle):
        bundle_path, _ = encoded_bundle
        pres = _load_pres_from_bundle(bundle_path)
        fake_assets = [
            {"type": "image", "size_mb": 1.0},
            {"type": "image", "size_mb": 2.0},
        ]
        manifest = build_manifest(bundle_path, pres, fake_assets)
        assert manifest["asset_summary"]["total_count"] == 2
        assert abs(manifest["asset_summary"]["total_size_mb"] - 3.0) < 0.01


# ── decode ────────────────────────────────────────────────────────────────────

class TestDecode:
    def test_creates_presentation_json(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=False)
        assert os.path.exists(os.path.join(out, "presentation.json"))

    def test_creates_manifest_json(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=False)
        assert os.path.exists(os.path.join(out, "manifest.json"))

    def test_presentation_json_is_valid_json(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=False)
        with open(os.path.join(out, "presentation.json")) as f:
            data = json.load(f)
        assert isinstance(data, dict)

    def test_manifest_json_is_valid_json(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=False)
        with open(os.path.join(out, "manifest.json")) as f:
            data = json.load(f)
        assert "presentation" in data

    def test_extract_true_writes_asset_files(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=True)
        assets_dir = Path(out) / "assets"
        all_files = [f for f in assets_dir.rglob("*") if f.is_file()]
        assert len(all_files) > 0

    def test_extract_false_skips_media(self, tmp_path, encoded_bundle):
        bundle_path, _ = encoded_bundle
        out = str(tmp_path / "decoded")
        decode(bundle_path, out, extract=False)
        media_dir = Path(out) / "assets" / "Media"
        assert not media_dir.exists()

    def test_no_pro_file_exits(self, tmp_path):
        bundle = tmp_path / "empty.probundle"
        bundle.write_bytes(make_probundle({"not_a_pro.txt": b"\x00"}))
        with pytest.raises(SystemExit):
            decode(str(bundle), str(tmp_path / "out"), extract=False)


# ── helpers ───────────────────────────────────────────────────────────────────

def _load_pres_from_bundle(bundle_path: str):
    from pco_types import presentation_pb2 as pp7
    with zipfile.ZipFile(bundle_path) as zf:
        pro_name = next(n for n in zf.namelist() if n.endswith(".pro"))
        data = zf.read(pro_name)
    pres = pp7.Presentation()
    pres.ParseFromString(data)
    return pres
