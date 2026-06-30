"""Encode a directory of media files into a ProPresenter 7 .probundle."""

import os
import sys
import struct
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
from urllib.parse import quote as url_quote

from google.protobuf.json_format import MessageToJson

from pco_types import presentation_pb2 as pp7

# ── enum constants ────────────────────────────────────────────────────────────
ACTION_TYPE_PRESENTATION_SLIDE = 11
ACTION_TYPE_MEDIA = 2
LAYER_TYPE_FOREGROUND = 1
COMPLETION_ACTION_TYPE_LAST = 1
END_BEHAVIOR_STOP_ON_CLEAR = 2
PLATFORM_MACOS = 1
APPLICATION_PROPRESENTER = 1
ROOT_SHOW = 10  # URL.LocalRelativePath.Root

# ── format tables ─────────────────────────────────────────────────────────────
_IMAGE_FMT = {
    ".tif": "tiff",
    ".tiff": "tiff",
    ".png": "png",
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
}
_VIDEO_FMT = {
    ".mp4": "h264",
    ".m4v": "h264",
    ".mov": None,  # detect from file content
}
_PRORES_FOURCC = {
    b"ap4h",
    b"ap4x",
    b"apch",
    b"apcn",
    b"apcs",
    b"apco",
    b"aprh",
    b"aprn",
    b"apto",
}
_H265_FOURCC = {b"hvc1", b"hev1", b"dvh1", b"dvhe"}

SUPPORTED_EXTS = set(_IMAGE_FMT) | set(_VIDEO_FMT)


# ══ media detection ════════════════════════════════════════════════════════════

def _detect_mov_format(path: str) -> str:
    """Detect codec in a .mov/.mp4 by scanning moov atom for codec FourCC."""
    try:
        size = os.path.getsize(path)
        read_size = min(512 * 1024, size)

        with open(path, "rb") as f:
            head = f.read(read_size)
            if size > read_size:
                f.seek(-read_size, 2)
                tail = f.read(read_size)
            else:
                tail = b""

        for buf in (head, tail):
            for cc in _PRORES_FOURCC:
                if cc in buf:
                    return "prores"
            for cc in _H265_FOURCC:
                if cc in buf:
                    return "hevc"
        return "h264"
    except OSError:
        return "h264"


def _detect_video_duration(path: str) -> float:
    """
    Parse the mvhd atom from a QuickTime/MP4 file to get duration in seconds.
    Returns 0.0 on failure (caller should prompt or use a default).
    """
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            # moov is usually at the end for .mov
            scan_size = min(2 * 1024 * 1024, size)
            f.seek(-scan_size, 2)
            tail = f.read(scan_size)

        idx = tail.rfind(b"mvhd")
        if idx < 0:
            # try head
            with open(path, "rb") as f:
                head = f.read(scan_size)
            idx = head.find(b"mvhd")
            if idx < 0:
                return 0.0
            tail = head

        payload = tail[idx + 8 :]  # skip 4-byte size + 4-byte 'mvhd'
        version = payload[0]
        if version == 0 and len(payload) >= 16:
            ts = int.from_bytes(payload[4:8], "big")
            dur = int.from_bytes(payload[8:12], "big")
        elif version == 1 and len(payload) >= 28:
            ts = int.from_bytes(payload[8:16], "big")
            dur = int.from_bytes(payload[16:24], "big")
        else:
            return 0.0

        return dur / ts if ts else 0.0
    except Exception:
        return 0.0


def _detect_image_size(path: str) -> tuple[float, float]:
    """Read width/height from TIFF/PNG/JPEG without Pillow."""
    ext = Path(path).suffix.lower()
    try:
        with open(path, "rb") as f:
            data = f.read(256)

        if ext in (".tif", ".tiff"):
            bo = "<" if data[:2] == b"II" else ">"
            ifd_off = struct.unpack_from(bo + "I", data, 4)[0]
            if ifd_off + 2 > len(data):
                return 1920.0, 1080.0
            n = struct.unpack_from(bo + "H", data, ifd_off)[0]
            w = h = None
            for i in range(min(n, 20)):
                pos = ifd_off + 2 + i * 12
                if pos + 12 > len(data):
                    break
                tag, typ, cnt, val = struct.unpack_from(bo + "HHII", data, pos)
                if tag == 256:
                    w = val & 0xFFFF if typ == 3 else val
                elif tag == 257:
                    h = val & 0xFFFF if typ == 3 else val
            if w and h:
                return float(w), float(h)

        elif ext == ".png":
            if data[1:4] == b"PNG":
                w = int.from_bytes(data[16:20], "big")
                h = int.from_bytes(data[20:24], "big")
                return float(w), float(h)

        elif ext in (".jpg", ".jpeg"):
            i = 2
            while i < len(data) - 4:
                if data[i] != 0xFF:
                    break
                marker = data[i + 1]
                seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
                if marker in (0xC0, 0xC1, 0xC2):
                    h = int.from_bytes(data[i + 5 : i + 7], "big")
                    w = int.from_bytes(data[i + 7 : i + 9], "big")
                    return float(w), float(h)
                i += 2 + seg_len
    except Exception:
        pass
    return 1920.0, 1080.0


# ══ slide model ════════════════════════════════════════════════════════════════

@dataclass
class Slide:
    """
    Describes one cue (one visible slide) in the presentation.

    media_path  : local filesystem path to the media file, or None for a blank cue
    label       : text label shown in PP7's slide panel (defaults to filename)
    duration    : video duration in seconds (auto-detected if omitted)
    frame_rate  : video frame rate (auto-detected or default 23.976)
    width/height: display dimensions (auto-detected from image; default 1920x1080 for video)
    """

    media_path: Optional[str]
    label: str = ""
    duration: float = 0.0
    frame_rate: float = 23.976024627685547
    width: float = 1920.0
    height: float = 1080.0

    # resolved in __post_init__
    media_type: str = field(default="", init=False)  # "image" | "video" | "blank"
    format_str: str = field(default="", init=False)  # PP7 metadata.format

    def __post_init__(self):
        if not self.media_path:
            self.media_type = "blank"
            self.format_str = ""
            return

        if not self.label:
            self.label = Path(self.media_path).name

        ext = Path(self.media_path).suffix.lower()

        if ext in _IMAGE_FMT:
            self.media_type = "image"
            self.format_str = _IMAGE_FMT[ext]
            w, h = _detect_image_size(self.media_path)
            if self.width == 1920.0 and self.height == 1080.0:
                self.width, self.height = w, h

        elif ext in _VIDEO_FMT:
            self.media_type = "video"
            fmt = _VIDEO_FMT[ext]
            if fmt is None:
                fmt = _detect_mov_format(self.media_path)
            self.format_str = fmt
            if self.duration == 0.0:
                self.duration = _detect_video_duration(self.media_path)

        else:
            raise ValueError(
                f"Unsupported extension: {ext!r}\n"
                f"Supported: {sorted(_IMAGE_FMT) + sorted(_VIDEO_FMT)}"
            )


# ══ proto builders ═════════════════════════════════════════════════════════════

def _new_uuid() -> str:
    return str(uuid.uuid4()).upper()


def _build_application_info(pres) -> None:
    """Fill in applicationInfo exactly as seen in the real bundle."""
    ai = pres.application_info
    ai.platform = PLATFORM_MACOS
    ai.platform_version.major_version = 26
    ai.platform_version.minor_version = 1
    ai.application = APPLICATION_PROPRESENTER
    ai.application_version.major_version = 20
    ai.application_version.patch_version = 1
    ai.application_version.build = "335544583"


def _build_blank_cue(pres) -> str:
    """Add a blank/spacer cue with a single empty slide action. Returns cue UUID."""
    cue = pres.cues.add()
    cue.uuid.string = _new_uuid()
    cue.completion_action_type = COMPLETION_ACTION_TYPE_LAST
    cue.isEnabled = True

    action = cue.actions.add()
    action.uuid.string = _new_uuid()
    action.isEnabled = True
    action.type = ACTION_TYPE_PRESENTATION_SLIDE
    action.slide.presentation.base_slide.size.width = 1920.0
    action.slide.presentation.base_slide.size.height = 1080.0
    action.slide.presentation.base_slide.uuid.string = _new_uuid()
    action.slide.presentation.chord_chart.platform = PLATFORM_MACOS

    return cue.uuid.string


def _build_media_cue(pres, slide: Slide) -> str:
    """Add a full image-or-video cue (empty canvas + foreground media). Returns cue UUID."""
    zip_path = f"Media/Assets/{Path(slide.media_path).name}"
    abs_str = (
        "file:///Library/Application%20Support/ProPresenter/Media/Assets/"
        + url_quote(Path(slide.media_path).name)
    )

    cue = pres.cues.add()
    cue.uuid.string = _new_uuid()
    cue.completion_action_type = COMPLETION_ACTION_TYPE_LAST
    cue.isEnabled = True

    # ── Action 1: empty canvas ────────────────────────────────────────────
    a1 = cue.actions.add()
    a1.uuid.string = _new_uuid()
    a1.label.text = slide.label
    a1.isEnabled = True
    a1.type = ACTION_TYPE_PRESENTATION_SLIDE
    a1.slide.presentation.base_slide.size.width = slide.width
    a1.slide.presentation.base_slide.size.height = slide.height
    a1.slide.presentation.base_slide.uuid.string = _new_uuid()
    a1.slide.presentation.chord_chart.platform = PLATFORM_MACOS

    # ── Action 2: foreground media ────────────────────────────────────────
    a2 = cue.actions.add()
    a2.uuid.string = _new_uuid()
    a2.isEnabled = True
    a2.type = ACTION_TYPE_MEDIA
    a2.media.layer_type = LAYER_TYPE_FOREGROUND

    el = a2.media.element
    el.uuid.string = _new_uuid()

    el.url.absolute_string = abs_str
    el.url.platform = PLATFORM_MACOS
    el.url.local.root = ROOT_SHOW
    el.url.local.path = zip_path

    el.metadata.format = slide.format_str

    if slide.media_type == "image":
        _fill_image_element(el, slide)
    else:
        _fill_video_element(el, slide)

    # empty audio sub-message (present in all real cues)
    a2.media.audio.SetInParent()

    return cue.uuid.string


def _fill_image_element(el, slide: Slide) -> None:
    """Populate the image sub-message on a media element."""
    d = el.image.drawing
    d.natural_size.width = slide.width
    d.natural_size.height = slide.height
    d.custom_image_bounds.origin.SetInParent()
    d.custom_image_bounds.size.SetInParent()
    d.crop_insets.SetInParent()


def _fill_video_element(el, slide: Slide) -> None:
    """Populate the video sub-message on a media element."""
    d = el.video.drawing
    d.natural_size.width = slide.width
    d.natural_size.height = slide.height
    d.custom_image_bounds.origin.SetInParent()
    d.custom_image_bounds.size.SetInParent()
    d.crop_insets.SetInParent()

    el.video.audio.volume = 1.0

    t = el.video.transport
    t.play_rate = 1.0
    t.out_point = slide.duration
    t.should_fade_in = True
    t.should_fade_out = True
    t.end_point = slide.duration
    t.times_to_loop = 1

    v = el.video.video
    v.frame_rate = slide.frame_rate
    v.thumbnail_position = -1.0
    v.end_behavior = END_BEHAVIOR_STOP_ON_CLEAR
    v.soft_loop_duration = 0.5


def build_presentation(name: str, slides: list[Slide]):
    """Build a Presentation proto with all slides in a single cue group."""
    pres = pp7.Presentation()
    _build_application_info(pres)

    pres.uuid.string = _new_uuid()
    pres.name = name

    pres.background.color.red = 1.0
    pres.background.color.green = 1.0
    pres.background.color.blue = 1.0
    pres.background.color.alpha = 1.0

    pres.chord_chart.platform = PLATFORM_MACOS

    cue_uuids = [
        _build_blank_cue(pres) if slide.media_type == "blank"
        else _build_media_cue(pres, slide)
        for slide in slides
    ]

    cg = pres.cue_groups.add()
    cg.group.uuid.string = _new_uuid()
    cg.group.hotKey.SetInParent()
    for uid in cue_uuids:
        cg.cue_identifiers.add().string = uid

    return pres


# ══ bundle writer ══════════════════════════════════════════════════════════════

def write_bundle(pro_bytes: bytes, name: str, slides: list[Slide], output_path: str) -> None:
    """Write the .probundle ZIP: .pro at root + Media/Assets/* for each slide."""
    with zipfile.ZipFile(
        output_path, "w", compression=zipfile.ZIP_STORED, allowZip64=True
    ) as zf:
        zf.writestr(name + ".pro", pro_bytes)
        seen: set[str] = set()
        for slide in slides:
            if not slide.media_path:
                continue
            zip_path = f"Media/Assets/{Path(slide.media_path).name}"
            if zip_path not in seen:
                zf.write(slide.media_path, zip_path)
                seen.add(zip_path)


# ══ public API ═════════════════════════════════════════════════════════════════

def collect_slides(in_dir: str) -> list[Slide]:
    """
    Gather supported media files from in_dir, ordered by filename, as Slides.

    Ordering note: currently a simple case-insensitive name sort. See the README
    for ideas on sermon-aware ordering.
    """
    if not os.path.isdir(in_dir):
        print(f"ERROR: input directory not found: {in_dir!r}")
        sys.exit(1)

    paths = sorted(
        (
            os.path.join(in_dir, f)
            for f in os.listdir(in_dir)
            if Path(f).suffix.lower() in SUPPORTED_EXTS
            and os.path.isfile(os.path.join(in_dir, f))
        ),
        key=lambda p: Path(p).name.lower(),
    )

    if not paths:
        print(f"ERROR: no supported media files in {in_dir!r}")
        print(f"  Supported extensions: {sorted(SUPPORTED_EXTS)}")
        sys.exit(1)

    print(f"Found {len(paths)} media file(s) in {in_dir!r}:")
    for p in paths:
        print(f"  {Path(p).name}")

    return [Slide(media_path=p) for p in paths]


def encode(output_path: str, in_dir: str, name: str) -> None:
    """Build a .probundle (plus a .json sidecar) from every supported file in in_dir."""
    slides = collect_slides(in_dir)

    print(f"Building presentation: {name!r}  ({len(slides)} slides)")
    for s in slides:
        tag = f"{s.media_type}/{s.format_str}" if s.media_type != "blank" else "blank"
        size_mb = os.path.getsize(s.media_path) / 1024 / 1024 if s.media_path else 0
        dur = f"  dur={s.duration:.1f}s" if s.media_type == "video" else ""
        print(f"  {tag:16s}  {size_mb:7.1f} MB  {s.label}{dur}")

    pres = build_presentation(name, slides)
    pro_bytes = pres.SerializeToString()
    print(f"\n.pro size: {len(pro_bytes):,} bytes")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    write_bundle(pro_bytes, name, slides, output_path)

    total_mb = sum(
        os.path.getsize(s.media_path) / 1024 / 1024 for s in slides if s.media_path
    )
    print(f"Written: {output_path}  ({total_mb + len(pro_bytes) / 1024 / 1024:.1f} MB total)")

    json_path = output_path + ".json"
    with open(json_path, "w") as f:
        f.write(MessageToJson(pres, indent=2))
    print(f"Written: {json_path}")
