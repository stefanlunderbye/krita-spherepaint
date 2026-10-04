"""GPano XMP metadata so 360° viewers recognise an equirectangular JPEG.

Facebook, Google Photos, Kuula and similar viewers look for the Google Photo
Sphere (GPano) properties in the JPEG's XMP packet. Pure Python, no Krita needed.
"""
import struct

XMP_SIGNATURE = b"http://ns.adobe.com/xap/1.0/\x00"
_SOI = b"\xff\xd8"
_APP0, _APP1, _SOS = 0xE0, 0xE1, 0xDA


def gpano_packet(width, height):
    """The XMP packet describing a full 360°×180° equirectangular panorama."""
    return f"""<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/" x:xmptk="SpherePaint">
 <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
  <rdf:Description rdf:about="" xmlns:GPano="http://ns.google.com/photos/1.0/panorama/">
   <GPano:ProjectionType>equirectangular</GPano:ProjectionType>
   <GPano:UsePanoramaViewer>True</GPano:UsePanoramaViewer>
   <GPano:FullPanoWidthPixels>{width}</GPano:FullPanoWidthPixels>
   <GPano:FullPanoHeightPixels>{height}</GPano:FullPanoHeightPixels>
   <GPano:CroppedAreaImageWidthPixels>{width}</GPano:CroppedAreaImageWidthPixels>
   <GPano:CroppedAreaImageHeightPixels>{height}</GPano:CroppedAreaImageHeightPixels>
   <GPano:CroppedAreaLeftPixels>0</GPano:CroppedAreaLeftPixels>
   <GPano:CroppedAreaTopPixels>0</GPano:CroppedAreaTopPixels>
  </rdf:Description>
 </rdf:RDF>
</x:xmpmeta>
<?xpacket end="w"?>""".encode("utf-8")


def _segments(jpeg):
    """Splits a JPEG into (marker, payload) header segments and the remaining scan data."""
    if not jpeg.startswith(_SOI):
        raise ValueError("not a JPEG file")
    pos, segments = 2, []
    while pos + 4 <= len(jpeg):
        if jpeg[pos] != 0xFF:
            raise ValueError("corrupt JPEG segment structure")
        marker = jpeg[pos + 1]
        if marker == _SOS:
            break
        length = struct.unpack(">H", jpeg[pos + 2:pos + 4])[0]
        segments.append((marker, jpeg[pos + 4:pos + 2 + length]))
        pos += 2 + length
    return segments, jpeg[pos:]


def add_gpano(jpeg, width, height):
    """Returns the JPEG bytes with GPano XMP inserted, replacing any existing XMP packet."""
    segments, rest = _segments(jpeg)
    segments = [(m, p) for m, p in segments if not (m == _APP1 and p.startswith(XMP_SIGNATURE))]
    payload = XMP_SIGNATURE + gpano_packet(width, height)
    if len(payload) + 2 > 0xFFFF:
        raise ValueError("XMP packet too large")
    # Keep JFIF (APP0) and Exif (APP1) first, as many readers expect, then the XMP.
    insert_at = 0
    while insert_at < len(segments) and segments[insert_at][0] in (_APP0, _APP1):
        insert_at += 1
    segments.insert(insert_at, (_APP1, payload))
    out = bytearray(_SOI)
    for marker, data in segments:
        out += bytes((0xFF, marker)) + struct.pack(">H", len(data) + 2) + data
    return bytes(out + rest)
