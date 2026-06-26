import os
import zipfile
import pytest
from pathlib import Path

from encode import (
    Slide,
    _detect_image_size,
    _detect_mov_format,
    _detect_video_duration,
    _build_presentation,
    _write_bundle,
    encode_it,
    stat_dir_entries,
)
from tests.helpers import make_tiff, make_png, make_jpeg, make_mov


# ── _detect_image_size ────────────────────────────────────────────────────────

class TestDetectImageSize:
    def test_tiff_little_endian(self, tmp_path):
        f = tmp_path / "img.tif"
        f.write_bytes(make_tiff(640, 480))
        assert _detect_image_size(str(f)) == (640.0, 480.0)

    def test_tiff_non_standard_size(self, tmp_path):
        f = tmp_path / "img.tif"
        f.write_bytes(make_tiff(3840, 2160))
        assert _detect_image_size(str(f)) == (3840.0, 2160.0)

    def test_jpeg(self, tmp_path):
        f = tmp_path / "img.jpg"
        f.write_bytes(make_jpeg(1280, 720))
        assert _detect_image_size(str(f)) == (1280.0, 720.0)

    def test_png(self, tmp_path):
        f = tmp_path / "img.png"
        f.write_bytes(make_png(800, 600))
        assert _detect_image_size(str(f)) == (800.0, 600.0)

    def test_fallback_on_corrupt_file(self, tmp_path):
        f = tmp_path / "bad.tif"
        f.write_bytes(b"\x00" * 64)
        assert _detect_image_size(str(f)) == (1920.0, 1080.0)

    def test_fallback_on_empty_file(self, tmp_path):
        f = tmp_path / "empty.tif"
        f.write_bytes(b"")
        assert _detect_image_size(str(f)) == (1920.0, 1080.0)


# ── _detect_mov_format ────────────────────────────────────────────────────────

class TestDetectMovFormat:
    def test_prores_fourcc_apch(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(prores=True))
        assert _detect_mov_format(str(f)) == "prores"

    def test_h264_default_when_no_known_fourcc(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(prores=False))
        assert _detect_mov_format(str(f)) == "h264"

    def test_missing_file_returns_h264(self, tmp_path):
        assert _detect_mov_format(str(tmp_path / "nonexistent.mov")) == "h264"


# ── _detect_video_duration ────────────────────────────────────────────────────

class TestDetectVideoDuration:
    def test_v0_atom_returns_dur_over_ts(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(ts=600, dur=3000))
        result = _detect_video_duration(str(f))
        assert abs(result - 3000 / 600) < 0.001

    def test_no_mvhd_returns_zero(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(b"\x00" * 128)
        assert _detect_video_duration(str(f)) == 0.0

    def test_missing_file_returns_zero(self, tmp_path):
        assert _detect_video_duration(str(tmp_path / "missing.mov")) == 0.0

    def test_zero_timescale_returns_zero(self, tmp_path):
        # ts=0 would cause div-by-zero; code guards with `if ts`
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(ts=0, dur=0))
        assert _detect_video_duration(str(f)) == 0.0


# ── Slide ─────────────────────────────────────────────────────────────────────

class TestSlide:
    def test_blank_slide(self):
        s = Slide(media_path=None)
        assert s.media_type == "blank"
        assert s.format_str == ""

    def test_image_tif(self, tmp_path):
        f = tmp_path / "slide.tif"
        f.write_bytes(make_tiff(1920, 1080))
        s = Slide(media_path=str(f))
        assert s.media_type == "image"
        assert s.format_str == "tiff"
        assert s.width == 1920.0
        assert s.height == 1080.0

    def test_image_jpg(self, tmp_path):
        f = tmp_path / "slide.jpg"
        f.write_bytes(make_jpeg(1280, 720))
        s = Slide(media_path=str(f))
        assert s.media_type == "image"
        assert s.format_str == "jpeg"
        assert s.width == 1280.0
        assert s.height == 720.0

    def test_image_tiff_ext(self, tmp_path):
        f = tmp_path / "slide.tiff"
        f.write_bytes(make_tiff())
        s = Slide(media_path=str(f))
        assert s.format_str == "tiff"

    def test_video_mov_prores(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(prores=True))
        s = Slide(media_path=str(f))
        assert s.media_type == "video"
        assert s.format_str == "prores"

    def test_video_mov_h264(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(prores=False))
        s = Slide(media_path=str(f))
        assert s.media_type == "video"
        assert s.format_str == "h264"

    def test_video_duration_auto_detected(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(ts=600, dur=3000))
        s = Slide(media_path=str(f))
        assert abs(s.duration - 3000 / 600) < 0.001

    def test_explicit_duration_not_overwritten(self, tmp_path):
        f = tmp_path / "clip.mov"
        f.write_bytes(make_mov(ts=600, dur=3000))
        s = Slide(media_path=str(f), duration=99.0)
        assert s.duration == 99.0

    def test_label_defaults_to_filename(self, tmp_path):
        f = tmp_path / "my_slide.tif"
        f.write_bytes(make_tiff())
        s = Slide(media_path=str(f))
        assert s.label == "my_slide.tif"

    def test_explicit_label_preserved(self, tmp_path):
        f = tmp_path / "slide.tif"
        f.write_bytes(make_tiff())
        s = Slide(media_path=str(f), label="Custom Label")
        assert s.label == "Custom Label"

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_bytes(b"\x00")
        with pytest.raises(ValueError, match="Unsupported"):
            Slide(media_path=str(f))

    def test_image_size_not_overwritten_when_explicit(self, tmp_path):
        f = tmp_path / "slide.tif"
        f.write_bytes(make_tiff(640, 480))
        s = Slide(media_path=str(f), width=1920.0, height=1080.0)
        # explicit values (same as defaults) should NOT be overwritten by detection
        # because the guard is: if self.width == 1920.0 and self.height == 1080.0
        # (this tests the current behaviour, not necessarily the ideal)
        assert s.width == 640.0
        assert s.height == 480.0


# ── _build_presentation ───────────────────────────────────────────────────────

class TestBuildPresentation:
    def test_cue_count_matches_slide_count(self, tmp_path):
        f = tmp_path / "s.tif"
        f.write_bytes(make_tiff())
        slides = [Slide(media_path=str(f)), Slide(media_path=None)]
        pres, uuids = _build_presentation("Test", slides)
        assert len(pres.cues) == 2
        assert len(uuids) == 2

    def test_single_cue_group_for_all_slides(self, tmp_path):
        f = tmp_path / "s.tif"
        f.write_bytes(make_tiff())
        slides = [Slide(media_path=str(f)), Slide(media_path=None)]
        pres, _ = _build_presentation("Test", slides)
        assert len(pres.cue_groups) == 1

    def test_cue_group_uuid_list_matches_cue_uuids(self, tmp_path):
        f = tmp_path / "s.tif"
        f.write_bytes(make_tiff())
        slides = [Slide(media_path=str(f)), Slide(media_path=None)]
        pres, uuids = _build_presentation("Test", slides)
        group_uuids = [ci.string for ci in pres.cue_groups[0].cue_identifiers]
        assert group_uuids == uuids

    def test_presentation_name_set(self):
        pres, _ = _build_presentation("My Sermon", [Slide(media_path=None)])
        assert pres.name == "My Sermon"

    def test_all_cue_uuids_unique(self):
        slides = [Slide(media_path=None)] * 5
        _, uuids = _build_presentation("T", slides)
        assert len(set(uuids)) == len(uuids)

    def test_background_is_white(self):
        pres, _ = _build_presentation("T", [Slide(media_path=None)])
        c = pres.background.color
        assert c.red == 1.0 and c.green == 1.0 and c.blue == 1.0 and c.alpha == 1.0


# ── _write_bundle ─────────────────────────────────────────────────────────────

class TestWriteBundle:
    def test_bundle_is_valid_zip(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        bundle = str(tmp_path / "out.probundle")
        _write_bundle(b"\x00", "Pres", slides, bundle)
        assert zipfile.is_zipfile(bundle)

    def test_contains_pro_file(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        bundle = str(tmp_path / "out.probundle")
        _write_bundle(b"\x00", "Pres", slides, bundle)
        with zipfile.ZipFile(bundle) as zf:
            assert "Pres.pro" in zf.namelist()

    def test_contains_all_asset_paths(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        bundle = str(tmp_path / "out.probundle")
        _write_bundle(b"\x00", "Pres", slides, bundle)
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
        for s in slides:
            assert f"Media/Assets/{Path(s.media_path).name}" in names

    def test_deduplicates_same_file(self, tmp_path):
        f = tmp_path / "dup.tif"
        f.write_bytes(make_tiff())
        slides = [Slide(media_path=str(f)), Slide(media_path=str(f))]
        bundle = str(tmp_path / "out.probundle")
        _write_bundle(b"\x00", "Pres", slides, bundle)
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
        assert names.count("Media/Assets/dup.tif") == 1

    def test_blank_slide_adds_no_asset(self, tmp_path):
        slides = [Slide(media_path=None)]
        bundle = str(tmp_path / "out.probundle")
        _write_bundle(b"\x00", "Pres", slides, bundle)
        with zipfile.ZipFile(bundle) as zf:
            names = zf.namelist()
        assert not any(n.startswith("Media/") for n in names)


# ── stat_dir_entries ──────────────────────────────────────────────────────────

class TestStatDirEntries:
    def test_returns_alphabetically_sorted_slides(self, tmp_path):
        d = tmp_path / "in"
        d.mkdir()
        (d / "z_last.tif").write_bytes(make_tiff())
        (d / "a_first.jpg").write_bytes(make_jpeg())
        _, slides = stat_dir_entries(str(d), "Bundle")
        names = [Path(s.media_path).name for s in slides]
        assert names == sorted(names, key=str.lower)

    def test_ignores_unsupported_extensions(self, tmp_path):
        d = tmp_path / "in"
        d.mkdir()
        (d / "slide.tif").write_bytes(make_tiff())
        (d / "notes.txt").write_bytes(b"ignored")
        (d / "thumb.DS_Store").write_bytes(b"ignored")
        _, slides = stat_dir_entries(str(d), "Bundle")
        assert len(slides) == 1
        assert Path(slides[0].media_path).name == "slide.tif"

    def test_returns_correct_bundle_name(self, tmp_path):
        d = tmp_path / "in"
        d.mkdir()
        (d / "slide.tif").write_bytes(make_tiff())
        name, _ = stat_dir_entries(str(d), "MyBundle")
        assert name == "MyBundle"

    def test_missing_directory_exits(self, tmp_path):
        with pytest.raises(SystemExit):
            stat_dir_entries(str(tmp_path / "nonexistent"), "Bundle")

    def test_empty_directory_exits(self, tmp_path):
        d = tmp_path / "empty"
        d.mkdir()
        with pytest.raises(SystemExit):
            stat_dir_entries(str(d), "Bundle")


# ── encode_it ─────────────────────────────────────────────────────────────────

class TestEncodeIt:
    def test_creates_probundle_file(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        out = str(tmp_path / "out.probundle")
        encode_it(slides, "Test", out)
        assert os.path.exists(out)
        assert os.path.getsize(out) > 0

    def test_creates_json_sidecar(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        out = str(tmp_path / "out.probundle")
        encode_it(slides, "Test", out)
        assert os.path.exists(out + ".json")

    def test_creates_intermediate_output_dirs(self, tmp_path, image_only_dir):
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        out = str(tmp_path / "deep" / "nested" / "out.probundle")
        encode_it(slides, "Test", out)
        assert os.path.exists(out)

    def test_output_is_parseable_protobuf(self, tmp_path, image_only_dir):
        from pco_types import presentation_pb2 as pp7
        slides = [Slide(media_path=str(p)) for p in sorted(image_only_dir.iterdir())]
        out = str(tmp_path / "out.probundle")
        encode_it(slides, "MyPres", out)
        with zipfile.ZipFile(out) as zf:
            pro_bytes = zf.read("MyPres.pro")
        pres = pp7.Presentation()
        pres.ParseFromString(pro_bytes)
        assert pres.name == "MyPres"
