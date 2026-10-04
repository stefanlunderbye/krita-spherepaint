"""Omprojektion mellan equirectangular (2:1) och en gnomonisk perspektivvy.

Koordinatsystem: x höger, y upp, z framåt. Longitud 0 = bildens mitt,
latitud +90° = bildens överkant. Ren numpy, inget Krita-beroende, så
modulen går att testa fristående.
"""
import math

import numpy as np

# Antal rader som bearbetas åt gången, så att minnet hålls nere även för 8K+.
CHUNK_ROWS = 256


class View:
    """En kamera i sfärens mitt: girvinkel, lutning och synfält i grader."""

    def __init__(self, yaw_deg, pitch_deg, fov_deg, size):
        self.yaw = math.radians(yaw_deg)
        self.pitch = math.radians(pitch_deg)
        self.fov = math.radians(fov_deg)
        self.size = int(size)
        self.focal = (self.size / 2.0) / math.tan(self.fov / 2.0)

    def cam_to_world(self, x, y, z):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        y1 = y * cp + z * sp
        z1 = -y * sp + z * cp
        return x * cy + z1 * sy, y1, -x * sy + z1 * cy

    def world_to_cam(self, x, y, z):
        cp, sp = math.cos(self.pitch), math.sin(self.pitch)
        cy, sy = math.cos(self.yaw), math.sin(self.yaw)
        x1 = x * cy - z * sy
        z1 = x * sy + z * cy
        return x1, y * cp - z1 * sp, y * sp + z1 * cp


def matching_view_size(equirect_width, fov_deg):
    """Vystorlek som ger samma pixeltäthet i vyns mitt som i equirect-bilden."""
    n = 2.0 * math.tan(math.radians(fov_deg) / 2.0) * equirect_width / (2.0 * math.pi)
    return int(min(8192, max(256, round(n))))


def _sample(img, sx, sy, wrap_x):
    """Bilinjär sampling av img (H, W, C) i flyttalskoordinater; ger float32."""
    h, w = img.shape[:2]
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    fx = (sx - x0).astype(np.float32)[..., None]
    fy = (sy - y0).astype(np.float32)[..., None]
    x1 = x0 + 1
    y1 = y0 + 1
    if wrap_x:
        x0 %= w
        x1 %= w
    else:
        np.clip(x0, 0, w - 1, out=x0)
        np.clip(x1, 0, w - 1, out=x1)
    np.clip(y0, 0, h - 1, out=y0)
    np.clip(y1, 0, h - 1, out=y1)
    top = img[y0, x0].astype(np.float32) * (1 - fx) + img[y0, x1].astype(np.float32) * fx
    bottom = img[y1, x0].astype(np.float32) * (1 - fx) + img[y1, x1].astype(np.float32) * fx
    return top * (1 - fy) + bottom * fy


def _to_dtype(values, dtype):
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return np.clip(np.rint(values), info.min, info.max).astype(dtype)
    return values.astype(dtype)


def equirect_to_view(equirect, view):
    """Räknar fram perspektivvyn (size, size, C) ur en equirect-bild (H, W, C)."""
    h, w, c = equirect.shape
    n = view.size
    out = np.empty((n, n, c), dtype=equirect.dtype)
    u = np.arange(n, dtype=np.float64) + 0.5 - n / 2.0
    for r0 in range(0, n, CHUNK_ROWS):
        r1 = min(n, r0 + CHUNK_ROWS)
        v = -(np.arange(r0, r1, dtype=np.float64) + 0.5 - n / 2.0)
        cx, cy = np.meshgrid(u, v)
        cz = np.full_like(cx, view.focal)
        norm = np.sqrt(cx * cx + cy * cy + cz * cz)
        wx, wy, wz = view.cam_to_world(cx / norm, cy / norm, cz / norm)
        lon = np.arctan2(wx, wz)
        lat = np.arcsin(np.clip(wy, -1.0, 1.0))
        sx = (lon + math.pi) / (2 * math.pi) * w - 0.5
        sy = (math.pi / 2 - lat) / math.pi * h - 0.5
        out[r0:r1] = _to_dtype(_sample(equirect, sx, sy, wrap_x=True), equirect.dtype)
    return out


def view_to_equirect_rows(view_img, mask, view, width, height, r0, r1):
    """Projicerar tillbaka vyn för equirect-raderna r0..r1.

    Ger (vikt, färg) för raderna, där vikt (rows, width) är 0 där vyn inte
    ändrat något och färg (rows, width, C) är float32. None om inget i
    raderna berörs, så att anroparen kan hoppa över dem.
    """
    n = view.size
    x = (np.arange(width, dtype=np.float64) + 0.5) / width * 2 * math.pi - math.pi
    y = math.pi / 2 - (np.arange(r0, r1, dtype=np.float64) + 0.5) / height * math.pi
    lon, lat = np.meshgrid(x, y)
    clat = np.cos(lat)
    cx, cy, cz = view.world_to_cam(clat * np.sin(lon), np.sin(lat), clat * np.cos(lon))
    front = cz > 1e-6
    if not front.any():
        return None
    cz_safe = np.where(front, cz, 1.0)
    u = view.focal * cx / cz_safe + n / 2.0 - 0.5
    v = -view.focal * cy / cz_safe + n / 2.0 - 0.5
    inside = front & (u >= 0) & (u <= n - 1) & (v >= 0) & (v <= n - 1)
    if not inside.any():
        return None
    weight = np.zeros(lon.shape, dtype=np.float32)
    weight[inside] = _sample(mask[..., None], u[inside], v[inside], wrap_x=False)[:, 0]
    if not (weight > 0).any():
        return None
    color = np.zeros(lon.shape + (view_img.shape[2],), dtype=np.float32)
    touched = weight > 0
    color[touched] = _sample(view_img, u[touched], v[touched], wrap_x=False)
    return weight, color


def change_mask(before, after):
    """1.0 där någon kanal skiljer sig mellan två lika stora bilder, annars 0."""
    return np.any(before != after, axis=2).astype(np.float32)
