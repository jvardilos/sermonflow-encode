"""Decode a ProPresenter 7 .probundle back into media files + JSON metadata."""

import os
import struct
import sys
import json
from pathlib import Path

from helpers import current_iso_time

from pco_types import presentation_pb2 as pp7
from google.protobuf.json_format import MessageToJson

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".m4v"}
AUDIO_EXTS = {".mp3", ".wav", ".aac", ".m4a"}


def _media_type(ext: str) -> str:
    if ext in IMAGE_EXTS:
        return "image"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    return "other"


# ══ ZIP scanning ═══════════════════════════════════════════════════════════════

def _scan_zip_entries(bundle_path: str):
    """
    Scan ZIP local file headers by seeking, yielding (filename, data_offset, data_size).

    Python's zipfile rejects PP7-exported bundles because the ZIP64 end-of-central-
    directory record is offset by 98 bytes. We scan local file headers (LFH) directly —
    these are always intact. Since all PP7 files are STORED (uncompressed), after
    parsing each LFH we can seek directly over the data to the next header, making
    the scan O(number of files) rather than O(file size).
    """
    LFH_SIG = b"PK\x03\x04"
    CHUNK = 8 * 1024 * 1024  # 8 MB read window for signature search

    with open(bundle_path, "rb") as f:
        file_size = f.seek(0, 2)
        f.seek(0)
        pos = 0

        while pos < file_size - 30:
            f.seek(pos)
            window = f.read(min(CHUNK, file_size - pos))
            if not window:
                break

            sig_off = window.find(LFH_SIG)
            if sig_off == -1:
                # No LFH in this window — skip ahead (keep last 3 bytes for boundary hits)
                pos += max(1, len(window) - 3)
                continue

            pos += sig_off
            f.seek(pos)

            raw = f.read(30)
            if len(raw) < 30:
                break

            try:
                (
                    _sig,
                    _version,
                    _flags,
                    compression,
                    _mod_time,
                    _mod_date,
                    _crc32,
                    comp_size,
                    uncomp_size,
                    fname_len,
                    extra_len,
                ) = struct.unpack("<4sHHHHHIIIHH", raw)
            except struct.error:
                pos += 1
                continue

            if fname_len == 0 or fname_len > 1024:
                pos += 1
                continue

            fname_bytes = f.read(fname_len)
            if len(fname_bytes) < fname_len:
                break
            try:
                filename = fname_bytes.decode("utf-8")
            except UnicodeDecodeError:
                filename = fname_bytes.decode("latin-1")

            extra = f.read(extra_len)
            data_offset = pos + 30 + fname_len + extra_len

            # ZIP64: read real sizes from extra field
            if comp_size == 0xFFFFFFFF or uncomp_size == 0xFFFFFFFF:
                ei = 0
                actual_comp = actual_uncomp = None
                while ei + 4 <= len(extra):
                    tag, sz = struct.unpack_from("<HH", extra, ei)
                    block = extra[ei + 4 : ei + 4 + sz]
                    if tag == 0x0001:
                        if len(block) >= 8:
                            actual_uncomp = struct.unpack_from("<Q", block, 0)[0]
                        if len(block) >= 16:
                            actual_comp = struct.unpack_from("<Q", block, 8)[0]
                        break
                    ei += 4 + sz
                if actual_comp is None:
                    pos = data_offset
                    continue
                comp_size = actual_comp

            if comp_size == 0 and uncomp_size == 0:
                pos = data_offset
                continue

            if compression != 0:
                pos = data_offset + comp_size
                continue

            yield filename, data_offset, comp_size
            pos = data_offset + comp_size


def _copy_entry(
    bundle_path: str,
    data_offset: int,
    data_size: int,
    dest_path: str,
    chunk: int = 8 * 1024 * 1024,
) -> None:
    """Copy data_size bytes from bundle_path at data_offset to dest_path in chunks."""
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    with open(bundle_path, "rb") as src, open(dest_path, "wb") as dst:
        src.seek(data_offset)
        remaining = data_size
        while remaining > 0:
            block = src.read(min(chunk, remaining))
            if not block:
                break
            dst.write(block)
            remaining -= len(block)


def _normalize_zip_path(internal_path: str) -> str:
    """Strip absolute macOS path prefix, keeping Media/Assets/... or bare filename."""
    norm = internal_path.lstrip("/")
    if "/" in norm:
        parts = norm.split("/")
        try:
            media_idx = next(i for i, p in enumerate(parts) if p == "Media")
            return "/".join(parts[media_idx:])
        except StopIteration:
            return parts[-1]
    return norm


def extract_bundle(bundle_path: str, output_dir: str) -> list[dict]:
    """
    Extract all files from the .probundle ZIP using seeking (no full-file read).

    Returns list of {filename, internal_path, saved_path, size_bytes}.
    """
    os.makedirs(output_dir, exist_ok=True)
    extracted = []

    for internal_path, data_offset, data_size in _scan_zip_entries(bundle_path):
        norm = _normalize_zip_path(internal_path)
        dest = os.path.join(output_dir, norm)
        _copy_entry(bundle_path, data_offset, data_size, dest)

        extracted.append(
            {
                "filename": os.path.basename(norm),
                "internal_path": internal_path,
                "saved_path": norm,
                "size_bytes": data_size,
            }
        )
        print(f"  extracted: {norm}  ({data_size / 1024 / 1024:.1f} MB)")

    return extracted


# ══ inventory & manifest ═══════════════════════════════════════════════════════

def inventory_assets(output_dir: str, pres) -> list[dict]:
    """Inventory all media files and cross-reference with cue actions."""
    assets = []
    for root, _dirs, files in os.walk(output_dir):
        for fname in sorted(files):
            if fname.endswith(".pro"):
                continue
            fpath = os.path.join(root, fname)
            ext = Path(fname).suffix.lower()
            assets.append(
                {
                    "filename": fname,
                    "path": os.path.relpath(fpath, output_dir),
                    "size_mb": round(os.path.getsize(fpath) / 1024 / 1024, 2),
                    "type": _media_type(ext),
                    "format": ext.lstrip("."),
                    "referenced_by_cues": [],
                }
            )

    # Cross-reference with .pro media actions
    asset_index = {a["filename"]: a for a in assets}
    for cue in pres.cues:
        for action in cue.actions:
            if action.WhichOneof("ActionTypeData") == "media":
                local_path = action.media.element.url.local.path
                fname = os.path.basename(local_path)
                if fname in asset_index:
                    asset_index[fname]["referenced_by_cues"].append(cue.uuid.string)

    return assets


def build_manifest(bundle_path: str, pres, assets: list[dict]) -> dict:
    bundle_size = os.path.getsize(bundle_path) / 1024 / 1024
    by_type: dict[str, int] = {}
    for a in assets:
        by_type[a["type"]] = by_type.get(a["type"], 0) + 1

    cue_groups = [
        {
            "group_uuid": cg.group.uuid.string,
            "cue_count": len(cg.cue_identifiers),
            "cue_uuids": [ci.string for ci in cg.cue_identifiers],
        }
        for cg in pres.cue_groups
    ]

    return {
        "bundle_file": os.path.basename(bundle_path),
        "bundle_size_mb": round(bundle_size, 2),
        "extracted_at": current_iso_time(),
        "pro_file": Path(bundle_path).stem + ".pro",
        "presentation": {
            "name": pres.name,
            "uuid": pres.uuid.string,
            "cue_count": len(pres.cues),
            "cue_group_count": len(pres.cue_groups),
            "has_transition": pres.HasField("transition"),
            "has_ccli": pres.HasField("ccli"),
        },
        "assets": assets,
        "asset_summary": {
            "total_count": len(assets),
            "total_size_mb": round(sum(a.get("size_mb", 0) for a in assets), 2),
            "by_type": by_type,
        },
        "cue_groups": cue_groups,
    }


# ══ orchestration ══════════════════════════════════════════════════════════════

def _load_presentation(out_dir: str):
    """Parse the first extracted .pro file into a Presentation proto."""
    pro_files = list(Path(out_dir).rglob("*.pro"))
    if not pro_files:
        print("ERROR: No .pro file extracted")
        sys.exit(1)

    pro_path = pro_files[0]
    print(f"Decoding: {pro_path}")
    pres = pp7.Presentation()
    pres.ParseFromString(pro_path.read_bytes())
    return pres


def _write_json(path: str, payload) -> None:
    with open(path, "w") as f:
        if isinstance(payload, str):
            f.write(payload)
        else:
            json.dump(payload, f, indent=2)
    print(f"Wrote: {path}")


def _print_summary(pres, assets: list[dict]) -> None:
    print("\n--- Summary ---")
    print(f"  Presentation: {pres.name}")
    print(f"  UUID:         {pres.uuid.string}")
    print(f"  Cues:         {len(pres.cues)}")
    print(f"  Cue groups:   {len(pres.cue_groups)}")
    for cg in pres.cue_groups:
        print(f"    Group {cg.group.uuid.string[:8]}... → {len(cg.cue_identifiers)} cues")
    print(f"  Assets referenced in .pro: {len(assets)}")
    for a in assets:
        label = f"  {a.get('size_mb', '?')} MB" if "size_mb" in a else ""
        print(f"    {a['type']:6s}  {a['filename']}{label}")


def decode(bundle: str, out_dir: str) -> None:
    """Extract a .probundle, decode its .pro, and write presentation + manifest JSON."""
    extract_bundle(bundle, os.path.join(out_dir, "assets"))

    pres = _load_presentation(out_dir)

    _write_json(os.path.join(out_dir, "presentation.json"), MessageToJson(pres, indent=2))

    assets = inventory_assets(os.path.join(out_dir, "assets"), pres)
    manifest = build_manifest(bundle, pres, assets)
    _write_json(os.path.join(out_dir, "manifest.json"), manifest)

    _print_summary(pres, assets)
