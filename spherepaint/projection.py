"""Reprojection between equirectangular (2:1) and a gnomonic perspective view.

Coordinate system: x right, y up, z forward. Longitude 0 = centre of the
image, latitude +90° = top edge of the image. Pure NumPy with no Krita
dependency, so the module can be tested on its own.
"""
import math

import numpy as np

# Rows processed at a time, to keep memory use down even for 8K+ images.
CHUNK_ROWS = 256


class View:
    """A camera at the centre of the sphere: yaw, pitch and horizontal field of view in degrees.

    The image is ``size`` pixels wide and ``height`` pixels high (square when
    ``height`` is omitted); the vertical field of view follows from the aspect ratio.
    """

    def __init__(self, yaw_deg, pitch_deg, fov_deg, size, height=None):
        self.yaw = math.radians(yaw_deg)
        self.pitch = math.radians(pitch_deg)
        self.fov = math.radians(fov_deg)
        self.size = self.width = int(size)
        self.height = int(height) if height else self.width
        self.focal = (self.width / 2.0) / math.tan(self.fov / 2.0)

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
    """View size that gives the same pixel density at the view's centre as the equirectangular image."""
    n = 2.0 * math.tan(math.radians(fov_deg) / 2.0) * equirect_width / (2.0 * math.pi)
    return int(min(8192, max(256, round(n))))


def view_height(width, aspect):
    """Height in pixels of a view ``width`` pixels wide with aspect ratio ``aspect`` (width / height)."""
    return max(16, int(round(width / aspect)))


def view_outline(view, samples_per_edge=16):
    """The view's border as (longitude, latitude) in degrees, walking around its edges.

    Used to draw the projected area on a thumbnail of the equirectangular image.
    """
    t = np.linspace(-1.0, 1.0, samples_per_edge, endpoint=False)
    ones = np.ones_like(t)
    # Image plane at distance 1; half-width tan(fov/2), half-height scaled by the aspect.
    # Walk top, right, bottom, left.
    u = np.concatenate([t, ones, -t, -ones])
    v = np.concatenate([ones, -t, -ones, t])
    half_w = math.tan(view.fov / 2.0)
    half_h = half_w * view.height / view.width
    x, y, z = u * half_w, v * half_h, np.ones_like(u)
    norm = np.sqrt(x * x + y * y + z * z)
    wx, wy, wz = view.cam_to_world(x / norm, y / norm, z / norm)
    lon = np.degrees(np.arctan2(wx, wz))
    lat = np.degrees(np.arcsin(np.clip(wy, -1.0, 1.0)))
    return lon, lat


def _sample(img, sx, sy, wrap_x):
    """Bilinear sampling of img (H, W, C) at floating-point coordinates; returns float32.

    Krita stores alpha as the last channel in every colour model, so images with
    two or more channels are interpolated with premultiplied alpha. Otherwise the
    colour of fully transparent pixels (usually black) bleeds into the edges of
    semi-transparent strokes as a dark halo.
    """
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
    corners = (img[y0, x0], img[y0, x1], img[y1, x0], img[y1, x1])
    weights = ((1 - fx) * (1 - fy), fx * (1 - fy), (1 - fx) * fy, fx * fy)
    if img.shape[2] < 2:
        return sum(c.astype(np.float32) * w for c, w in zip(corners, weights))
    alpha = np.zeros(fx.shape, dtype=np.float32)
    premultiplied = np.zeros(fx.shape[:-1] + (img.shape[2] - 1,), dtype=np.float32)
    for c, w in zip(corners, weights):
        c = c.astype(np.float32)
        a = c[..., -1:] * w
        alpha += a
        premultiplied += c[..., :-1] * a
    colour = np.divide(premultiplied, alpha, out=np.zeros_like(premultiplied), where=alpha > 0)
    return np.concatenate([colour, alpha], axis=-1)


def _to_dtype(values, dtype):
    if np.issubdtype(dtype, np.integer):
        info = np.iinfo(dtype)
        return np.clip(np.rint(values), info.min, info.max).astype(dtype)
    return values.astype(dtype)


def equirect_to_view(equirect, view):
    """Computes the perspective view (height, width, C) from an equirectangular image (H, W, C)."""
    h, w, c = equirect.shape
    vw, vh = view.width, view.height
    out = np.empty((vh, vw, c), dtype=equirect.dtype)
    u = np.arange(vw, dtype=np.float64) + 0.5 - vw / 2.0
    for r0 in range(0, vh, CHUNK_ROWS):
        r1 = min(vh, r0 + CHUNK_ROWS)
        v = -(np.arange(r0, r1, dtype=np.float64) + 0.5 - vh / 2.0)
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


def render_perspective(equirect, yaw_deg, pitch_deg, fov_deg, width, height):
    """A perspective view of any size, for previews; ``fov_deg`` is the horizontal field of view."""
    view = View(yaw_deg, pitch_deg, fov_deg, width, height)
    focal = view.focal
    h, w = equirect.shape[:2]
    cx, cy = np.meshgrid(np.arange(width, dtype=np.float64) + 0.5 - width / 2.0,
                         -(np.arange(height, dtype=np.float64) + 0.5 - height / 2.0))
    cz = np.full_like(cx, focal)
    norm = np.sqrt(cx * cx + cy * cy + cz * cz)
    wx, wy, wz = view.cam_to_world(cx / norm, cy / norm, cz / norm)
    sx = (np.arctan2(wx, wz) + math.pi) / (2 * math.pi) * w - 0.5
    sy = (math.pi / 2 - np.arcsin(np.clip(wy, -1.0, 1.0))) / math.pi * h - 0.5
    return _to_dtype(_sample(equirect, sx, sy, wrap_x=True), equirect.dtype)


def view_to_equirect_rows(view_img, mask, view, width, height, r0, r1):
    """Projects the view back onto equirectangular rows r0..r1.

    Returns (weight, colour) for the rows, where weight (rows, width) is 0
    wherever the view changed nothing and colour (rows, width, C) is float32.
    Returns None if the rows are not affected at all, so the caller can skip them.
    """
    vw, vh = view.width, view.height
    x = (np.arange(width, dtype=np.float64) + 0.5) / width * 2 * math.pi - math.pi
    y = math.pi / 2 - (np.arange(r0, r1, dtype=np.float64) + 0.5) / height * math.pi
    lon, lat = np.meshgrid(x, y)
    clat = np.cos(lat)
    cx, cy, cz = view.world_to_cam(clat * np.sin(lon), np.sin(lat), clat * np.cos(lon))
    front = cz > 1e-6
    if not front.any():
        return None
    cz_safe = np.where(front, cz, 1.0)
    u = view.focal * cx / cz_safe + vw / 2.0 - 0.5
    v = -view.focal * cy / cz_safe + vh / 2.0 - 0.5
    inside = front & (u >= 0) & (u <= vw - 1) & (v >= 0) & (v <= vh - 1)
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


# The six cube faces as (name, yaw, pitch); with a 90° field of view they tile the sphere.
CUBE_FACES = (
    ("front", 0, 0), ("right", 90, 0), ("back", 180, 0),
    ("left", -90, 0), ("top", 0, 90), ("bottom", 0, -90),
)


def cube_to_equirect(faces, width, height):
    """Assembles six square face images into an equirectangular image.

    ``faces`` is a list of (View, image) with 90° views, one per cube face, all
    images (n, n, C) of the same dtype. Every equirectangular pixel is sampled
    from the face it falls on, so the faces meet without seams or overlap.
    """
    dtype = faces[0][1].dtype
    channels = faces[0][1].shape[2]
    out = np.zeros((height, width, channels), dtype=dtype)
    x = (np.arange(width, dtype=np.float64) + 0.5) / width * 2 * math.pi - math.pi
    for r0 in range(0, height, CHUNK_ROWS):
        r1 = min(height, r0 + CHUNK_ROWS)
        y = math.pi / 2 - (np.arange(r0, r1, dtype=np.float64) + 0.5) / height * math.pi
        lon, lat = np.meshgrid(x, y)
        clat = np.cos(lat)
        wx, wy, wz = clat * np.sin(lon), np.sin(lat), clat * np.cos(lon)
        done = np.zeros(lon.shape, dtype=bool)
        chunk = out[r0:r1]
        for view, img in faces:
            n = img.shape[0]
            cx, cy, cz = view.world_to_cam(wx, wy, wz)
            on_face = ~done & (cz > 1e-9) & (np.abs(cx) <= cz) & (np.abs(cy) <= cz)
            if not on_face.any():
                continue
            focal = (n / 2.0) / math.tan(view.fov / 2.0)
            u = focal * cx[on_face] / cz[on_face] + n / 2.0 - 0.5
            v = -focal * cy[on_face] / cz[on_face] + n / 2.0 - 0.5
            chunk[on_face] = _to_dtype(_sample(img, u, v, wrap_x=False), dtype)
            done |= on_face
    return out


def equirect_to_cube(equirect, face_size):
    """Splits an equirectangular image into six (name, image) cube faces, as seen from the centre.

    Front/right/back/left are upright; the top face's image "up" points to the back
    and the bottom face's to the front, matching the views used by ``cube_to_equirect``.
    """
    return [(name, equirect_to_view(equirect, View(yaw, pitch, 90, face_size)))
            for name, yaw, pitch in CUBE_FACES]


def equirect_size_for_faces(face_size):
    """Equirectangular (width, height) with the same density as cube faces of ``face_size``."""
    width = 2 * max(1, round(math.pi * face_size / 2))
    return width, width // 2


def change_mask(before, after):
    """1.0 where any channel differs between two same-sized images, otherwise 0."""
    return np.any(before != after, axis=2).astype(np.float32)
