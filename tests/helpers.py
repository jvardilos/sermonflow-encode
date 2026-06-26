"""
Minimal synthetic binary file builders for tests.
Each produces the smallest valid header the detector code will accept.
"""
import io
import struct
import zipfile


def make_tiff(width: int = 1920, height: int = 1080) -> bytes:
    """Little-endian TIFF with ImageWidth/ImageLength IFD entries."""
    ifd_off = 8
    header = b"II" + struct.pack("<H", 42) + struct.pack("<I", ifd_off)
    entry_w = struct.pack("<HHII", 256, 3, 1, width)   # tag, SHORT, count, val
    entry_h = struct.pack("<HHII", 257, 3, 1, height)
    ifd = struct.pack("<H", 2) + entry_w + entry_h + struct.pack("<I", 0)
    return header + ifd + b"\x00" * 64


def make_png(width: int = 1920, height: int = 1080) -> bytes:
    """PNG with correct IHDR so bytes 16-24 carry width/height."""
    import zlib
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">II", width, height) + b"\x08\x02\x00\x00\x00"
    crc = struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data) & 0xFFFFFFFF)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + crc
    return sig + ihdr + b"\x00" * 64


def make_jpeg(width: int = 1920, height: int = 1080) -> bytes:
    """JPEG SOI + SOF0 so the detector reads width/height at i+7, i+5."""
    soi = b"\xff\xd8"
    # seg_len=11: 2(len)+1(prec)+2(h)+2(w)+1(ncomp)+3(comp_spec)
    sof0 = (
        b"\xff\xc0"
        + struct.pack(">H", 11)            # length
        + b"\x08"                           # precision
        + struct.pack(">HH", height, width) # h then w
        + b"\x01\x01\x11\x00"             # ncomp + one component spec
    )
    return soi + sof0 + b"\xff\xd9"


def make_mov(ts: int = 600, dur: int = 3000, prores: bool = False) -> bytes:
    """
    Minimal QuickTime .mov with an mvhd atom.

    _detect_video_duration reads payload starting at (mvhd_pos + 8), so:
      payload[4:8] = ts   (our modification_time field)
      payload[8:12] = dur (our timescale field)
    Expected return: dur / ts.
    """
    version_flags = b"\x00\x00\x00\x00"
    creation     = struct.pack(">I", 0)    # payload[0:4]
    modification = struct.pack(">I", ts)   # payload[4:8] → ts in detector
    timescale_f  = struct.pack(">I", dur)  # payload[8:12] → dur in detector
    rest = b"\x00" * 24

    atom_data = version_flags + creation + modification + timescale_f + rest
    mvhd = struct.pack(">I", 8 + len(atom_data)) + b"mvhd" + atom_data

    prefix = b"apch" + b"\x00" * 60 if prores else b"\x00" * 64
    return prefix + mvhd


def make_probundle(files: dict) -> bytes:
    """Build a valid uncompressed ZIP in memory. files: {zip_path: bytes}"""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_STORED) as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()
