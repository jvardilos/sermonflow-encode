import pytest
from tests.helpers import make_tiff, make_jpeg, make_mov


@pytest.fixture
def image_only_dir(tmp_path):
    """Two image files, alphabetically named."""
    d = tmp_path / "in"
    d.mkdir()
    (d / "slide1.tif").write_bytes(make_tiff(1920, 1080))
    (d / "slide2.jpg").write_bytes(make_jpeg(1280, 720))
    return d


@pytest.fixture
def mixed_media_dir(tmp_path):
    """Image + video, alphabetically named."""
    d = tmp_path / "in"
    d.mkdir()
    (d / "a_image.tif").write_bytes(make_tiff(640, 480))
    (d / "b_image.jpg").write_bytes(make_jpeg(1280, 720))
    (d / "c_video.mov").write_bytes(make_mov(ts=600, dur=3000))
    return d


@pytest.fixture
def encoded_bundle(tmp_path, image_only_dir):
    """Pre-built .probundle from image_only_dir. Returns (bundle_path, source_dir)."""
    from encode import encode
    bundle = str(tmp_path / "test.probundle")
    encode(bundle, str(image_only_dir), "TestPresentation")
    return bundle, image_only_dir
