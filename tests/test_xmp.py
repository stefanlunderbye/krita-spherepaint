"""GPano XMP insertion into JPEG files."""
import struct

import pytest

import xmp


def segment(marker, payload):
    return bytes((0xFF, marker)) + struct.pack(">H", len(payload) + 2) + payload


def fake_jpeg(*extra):
    """A structurally valid JPEG: SOI, JFIF, optional segments, a table, scan data and EOI."""
    jfif = segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
    dqt = segment(0xDB, bytes(65))
    scan = segment(0xDA, bytes(10)) + b"\x12\x34\xff\x00\x56" + b"\xff\xd9"
    return b"\xff\xd8" + jfif + b"".join(extra) + dqt + scan


def markers(jpeg):
    segments, _ = xmp._segments(jpeg)
    return [m for m, _ in segments]


def xmp_payloads(jpeg):
    segments, _ = xmp._segments(jpeg)
    return [p for m, p in segments if m == 0xE1 and p.startswith(xmp.XMP_SIGNATURE)]


def test_inserts_gpano_after_jfif_and_keeps_the_rest():
    original = fake_jpeg()
    out = xmp.add_gpano(original, 8192, 4096)
    assert markers(out) == [0xE0, 0xE1, 0xDB]
    packet = xmp_payloads(out)[0].decode("utf-8")
    assert "<GPano:ProjectionType>equirectangular</GPano:ProjectionType>" in packet
    assert "<GPano:FullPanoWidthPixels>8192</GPano:FullPanoWidthPixels>" in packet
    assert "<GPano:FullPanoHeightPixels>4096</GPano:FullPanoHeightPixels>" in packet
    assert out.endswith(original[original.index(b"\xff\xda"):])  # scan data untouched


def test_replaces_an_existing_xmp_packet_and_keeps_exif():
    exif = segment(0xE1, b"Exif\x00\x00" + bytes(20))
    old_xmp = segment(0xE1, xmp.XMP_SIGNATURE + b"<old/>")
    out = xmp.add_gpano(fake_jpeg(exif, old_xmp), 4000, 2000)
    payloads = xmp_payloads(out)
    assert len(payloads) == 1 and b"<old/>" not in payloads[0]
    segments, _ = xmp._segments(out)
    assert segments[1][1].startswith(b"Exif")  # Exif stays before the XMP


def test_is_idempotent():
    once = xmp.add_gpano(fake_jpeg(), 100, 50)
    assert xmp.add_gpano(once, 100, 50) == once


def test_rejects_non_jpeg_data():
    with pytest.raises(ValueError):
        xmp.add_gpano(b"\x89PNG\r\n", 2, 1)
