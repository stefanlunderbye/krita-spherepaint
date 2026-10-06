"""Tests for the reprojection maths (pure NumPy, no Krita needed)."""
import math

import numpy as np
import pytest

import projection as P

W, H = 1024, 512


def gradient_equirect():
    """Longitude in channel 0, latitude in channel 1, opaque alpha."""
    eq = np.zeros((H, W, 4), np.uint8)
    eq[..., 0] = np.linspace(0, 255, W)[None, :]
    eq[..., 1] = np.linspace(0, 255, H)[:, None]
    eq[..., 3] = 255
    return eq


def equirect_pixel(lon_deg, lat_deg):
    x = int((lon_deg + 180) / 360 * W) % W
    y = min(H - 1, int((90 - lat_deg) / 180 * H))
    return y, x


def write_back(eq, painted, mask, view):
    out = eq.copy()
    for r0 in range(0, H, P.CHUNK_ROWS):
        r1 = min(H, r0 + P.CHUNK_ROWS)
        result = P.view_to_equirect_rows(painted, mask, view, W, H, r0, r1)
        if result is None:
            continue
        weight, colour = result
        block = out[r0:r1].astype(np.float32)
        out[r0:r1] = P._to_dtype(block * (1 - weight[..., None]) + colour * weight[..., None], np.uint8)
    return out


# Not exactly 180°: there the centre sits on the seam, where the test gradient jumps
# from 255 to 0 and bilinear sampling correctly blends the two edges.
@pytest.mark.parametrize("yaw,pitch", [(0, 0), (90, 0), (-170, 20), (175, 0), (45, 60)])
def test_view_centre_looks_at_yaw_and_pitch(yaw, pitch):
    eq = gradient_equirect()
    view = P.View(yaw, pitch, 90, 257)
    img = P.equirect_to_view(eq, view)
    centre = img[view.size // 2, view.size // 2].astype(int)
    expected = eq[equirect_pixel(yaw, pitch)].astype(int)
    assert abs(centre[1] - expected[1]) <= 2  # latitude channel
    lon_error = abs(centre[0] - expected[0])
    assert min(lon_error, 255 - lon_error) <= 2  # longitude channel, wraps at the seam


@pytest.mark.parametrize("yaw,pitch,fov", [(0, 0, 90), (180, 0, 60), (-170, 20, 70), (45, 85, 90), (0, -89, 100)])
def test_painted_square_lands_at_view_centre_and_nothing_else_changes(yaw, pitch, fov):
    eq = gradient_equirect()
    view = P.View(yaw, pitch, fov, P.matching_view_size(W, fov))
    img = P.equirect_to_view(eq, view)
    painted = img.copy()
    c = view.size // 2
    painted[c - 8:c + 8, c - 8:c + 8] = (10, 20, 30, 255)
    out = write_back(eq, painted, P.change_mask(img, painted), view)

    assert tuple(out[equirect_pixel(yaw, pitch)][:3]) == (10, 20, 30)
    changed = np.any(out != eq, axis=2)
    assert changed.any()
    if abs(pitch) < 60:  # away from the poles the opposite side must be untouched
        far = (int((yaw + 180) / 360 * W) + W // 2) % W
        assert not changed[:, far].any()


def test_unchanged_view_writes_nothing_back():
    eq = gradient_equirect()
    view = P.View(30, 10, 90, 300)
    img = P.equirect_to_view(eq, view)
    mask = P.change_mask(img, img)
    assert not mask.any()
    assert all(P.view_to_equirect_rows(img, mask, view, W, H, r0, min(H, r0 + P.CHUNK_ROWS)) is None
               for r0 in range(0, H, P.CHUNK_ROWS))


def test_semi_transparent_edges_have_no_dark_halo():
    # Opaque red on a fully transparent black background, sampled across the edge.
    img = np.zeros((4, 4, 4), np.uint8)
    img[:, 2:] = (255, 0, 0, 255)
    sampled = P._sample(img, np.array([1.5]), np.array([1.0]), wrap_x=False)[0]
    assert sampled[0] == pytest.approx(255)  # colour stays red instead of darkening
    assert sampled[3] == pytest.approx(127.5)  # only alpha is blended


def test_single_channel_images_are_interpolated_directly():
    mask = np.array([[0.0, 1.0]], np.float32)[..., None]
    assert P._sample(mask, np.array([0.25]), np.array([0.0]), wrap_x=False)[0, 0] == pytest.approx(0.25)


def test_cube_faces_tile_the_sphere():
    colours = {name: (i * 40, 255 - i * 40, 7 * i, 255) for i, (name, _, _) in enumerate(P.CUBE_FACES)}
    faces = []
    for name, yaw, pitch in P.CUBE_FACES:
        img = np.empty((64, 64, 4), np.uint8)
        img[:] = colours[name]
        faces.append((P.View(yaw, pitch, 90, 64), img))
    eq = P.cube_to_equirect(faces, W, H)
    assert (eq[..., 3] == 255).all()  # every pixel belongs to a face

    def face_at(lon, lat):
        px = tuple(eq[equirect_pixel(lon, lat)])
        return next(name for name, c in colours.items() if c == px)

    assert face_at(0, 0) == "front"
    assert face_at(90, 0) == "right"
    assert face_at(179, 0) == face_at(-179, 0) == "back"
    assert face_at(-90, 0) == "left"
    assert face_at(0, 89) == "top"
    assert face_at(45, -89) == "bottom"
    assert face_at(44, 0) == "front" and face_at(46, 0) == "right"


@pytest.mark.parametrize("aspect", [16 / 9, 4 / 3, 3 / 4, 9 / 16])
def test_rectangular_view_round_trip(aspect):
    eq = gradient_equirect()
    width = 300
    view = P.View(30, 10, 90, width, P.view_height(width, aspect))
    img = P.equirect_to_view(eq, view)
    assert img.shape == (view.height, width, 4)
    painted = img.copy()
    cy, cx = view.height // 2, width // 2
    painted[cy - 6:cy + 6, cx - 6:cx + 6] = (10, 20, 30, 255)
    out = write_back(eq, painted, P.change_mask(img, painted), view)
    assert tuple(out[equirect_pixel(30, 10)][:3]) == (10, 20, 30)


def test_aspect_keeps_horizontal_fov_and_scales_vertical():
    wide = P.View(0, 0, 90, 320, 180)
    lon, lat = P.view_outline(wide, samples_per_edge=8)
    assert lon.max() == pytest.approx(45, abs=0.5)  # horizontal field of view unchanged
    expected_half_v = math.degrees(math.atan(math.tan(math.radians(45)) * 180 / 320))
    assert lat.max() == pytest.approx(expected_half_v, abs=0.5)  # vertical follows the aspect


def test_rectangular_preview_matches_the_projection():
    eq = gradient_equirect()
    view = P.View(-60, 5, 100, 240, 135)
    assert np.array_equal(P.render_perspective(eq, -60, 5, 100, 240, 135), P.equirect_to_view(eq, view))


def test_view_height_from_aspect():
    assert P.view_height(1600, 16 / 9) == 900
    assert P.view_height(900, 9 / 16) == 1600
    assert P.view_height(10, 16 / 9) == 16  # never degenerate


def test_preview_matches_the_projection_for_square_views():
    eq = gradient_equirect()
    view = P.View(40, -15, 80, 200)
    assert np.array_equal(P.render_perspective(eq, 40, -15, 80, 200, 200), P.equirect_to_view(eq, view))


def test_preview_can_be_rectangular():
    img = P.render_perspective(gradient_equirect(), 0, 0, 90, 160, 90)
    assert img.shape == (90, 160, 4)


def test_cube_round_trip_reproduces_the_panorama():
    rng = np.random.default_rng(1)
    # Smooth content so resampling error stays small: low-frequency colour waves.
    lon = np.linspace(0, 2 * np.pi, W, endpoint=False)[None, :]
    lat = np.linspace(0, np.pi, H)[:, None]
    eq = np.zeros((H, W, 4), np.uint8)
    eq[..., 0] = 127 + 100 * np.sin(lon) * np.sin(lat)
    eq[..., 1] = 127 + 100 * np.cos(2 * lat)
    eq[..., 2] = rng.integers(100, 110)
    eq[..., 3] = 255
    face_size = P.matching_view_size(W, 90)
    faces = P.equirect_to_cube(eq, face_size)
    assert [name for name, _ in faces] == [name for name, _, _ in P.CUBE_FACES]
    views = [(P.View(yaw, pitch, 90, face_size), img) for (_, yaw, pitch), (_, img) in zip(P.CUBE_FACES, faces)]
    back = P.cube_to_equirect(views, W, H)
    error = np.abs(back.astype(int) - eq.astype(int))[..., :3]
    assert error.mean() < 1.0
    assert np.percentile(error, 99) <= 4


def test_equirect_size_for_faces_matches_density():
    width, height = P.equirect_size_for_faces(1024)
    assert width == 2 * height
    assert abs(P.matching_view_size(width, 90) - 1024) <= 1


def test_view_outline_matches_camera_rays():
    view = P.View(20, 10, 90, 512)
    lon, lat = P.view_outline(view, samples_per_edge=8)
    x, y, z = view.cam_to_world(0.0, math.tan(view.fov / 2), 1.0)  # top-middle of the image plane
    n = math.sqrt(x * x + y * y + z * z)
    assert lon[4] == pytest.approx(math.degrees(math.atan2(x / n, z / n)))
    assert lat[4] == pytest.approx(math.degrees(math.asin(y / n)))


def test_world_and_camera_rotations_are_inverse():
    view = P.View(123, -37, 80, 100)
    v = np.array([0.3, -0.5, 0.81])
    back = view.world_to_cam(*view.cam_to_world(*v))
    assert np.allclose(back, v)


def test_matching_view_size_is_clamped():
    assert P.matching_view_size(100, 90) == 256
    assert P.matching_view_size(10 ** 6, 150) == 8192
    assert P.matching_view_size(8192, 90) == round(8192 / math.pi)


def noise_equirect(width=W, height=H, seed=3):
    """Random colours with partly transparent patches: any pixel read from outside a region shows."""
    rng = np.random.default_rng(seed)
    eq = rng.integers(0, 256, (height, width, 4), dtype=np.uint8)
    eq[..., 3] = 255
    eq[rng.random((height, width)) < 0.1, 3] = rng.integers(0, 255)
    return eq


def crop(eq, region):
    """The region (x0, y0, x1, y1) of eq, continuing across the seam when x1 > width."""
    x0, y0, x1, y1 = region
    return eq[y0:y1].take(np.arange(x0, x1) % eq.shape[1], axis=1)


VIEWS = [(0, 0, 90, 1, None), (178, 5, 70, 1, None), (-179, -30, 100, 16 / 9, None), (45, 80, 60, 1, None),
         (10, 87, 90, 3 / 4, None), (-120, -89, 90, 1, None), (90, 60, 120, 9 / 16, None),
         (179, 0, 90, 1, (300, 120, 360, 200)), (0, 75, 90, 1, (0, 0, 40, 30)), (30, -10, 50, 1, (10, 10, 12, 11))]


@pytest.mark.parametrize("yaw,pitch,fov,aspect,rect", VIEWS)
def test_projecting_from_the_view_region_matches_the_whole_panorama(yaw, pitch, fov, aspect, rect):
    eq = noise_equirect()
    view = P.View(yaw, pitch, fov, 400, P.view_height(400, aspect))
    region = P.view_region(view, W, H, rect)
    x0, y0, x1, y1 = region
    assert 0 <= x0 < W and x0 < x1 <= x0 + W and 0 <= y0 < y1 <= H
    expected = P.equirect_to_view(eq, view, rect=rect)
    assert np.array_equal(P.equirect_to_view(crop(eq, region), view, (x0, y0, W, H), rect), expected)
    if rect is None:
        assert np.array_equal(expected, P.equirect_to_view(eq, view))


def test_view_region_is_small_for_ordinary_views():
    x0, y0, x1, y1 = P.view_region(P.View(0, 0, 90, 1000), W, H)
    assert (x1 - x0) * (y1 - y0) < W * H / 6  # a 90° view covers about an eighth of the panorama
    x0, y0, x1, _ = P.view_region(P.View(0, 70, 90, 1000), W, H)
    assert (x0, y0, x1) == (0, 0, W)  # the north pole: all longitudes, from the top row


def test_projecting_several_images_at_once():
    eq = noise_equirect()
    view = P.View(40, 20, 80, 300)
    a, b = P.equirect_to_views([eq, eq[..., :1].copy()], view)
    assert np.array_equal(a, P.equirect_to_view(eq, view))
    assert np.array_equal(b, P.equirect_to_view(eq[..., :1].copy(), view))


@pytest.mark.parametrize("yaw,pitch,fov,aspect,rect", VIEWS)
def test_write_back_limited_to_the_painted_region_matches_the_full_scan(yaw, pitch, fov, aspect, rect):
    eq = noise_equirect()
    view = P.View(yaw, pitch, fov, 400, P.view_height(400, aspect))
    img = P.equirect_to_view(eq, view)
    painted = img.copy()
    left, top, right, bottom = rect or (150, 100, 260, 190)
    bottom = min(bottom, view.height)
    painted[top:bottom, left:right] = (10, 200, 30, 255)
    mask = P.change_mask(img, painted)
    full = write_back(eq, painted, mask, view)

    rows, cols = np.nonzero(mask.any(axis=1))[0], np.nonzero(mask.any(axis=0))[0]
    painted_rect = (max(0, cols[0] - 1), max(0, rows[0] - 1),
                    min(view.width, cols[-1] + 2), min(view.height, rows[-1] + 2))
    x0, y0, x1, y1 = P.view_region(view, W, H, painted_rect)
    out = eq.copy()
    result = P.view_to_equirect_rows(painted, mask, view, W, H, y0, y1, x0, x1)
    weight, colour = result
    columns = np.arange(x0, x1) % W
    block = out[y0:y1][:, columns].astype(np.float32)
    out[y0:y1, columns] = P._to_dtype(block * (1 - weight[..., None]) + colour * weight[..., None], np.uint8)
    assert np.array_equal(out, full)


@pytest.mark.parametrize("dtype,channels", [(np.uint8, 4), (np.uint16, 4), (np.float16, 4), (np.float32, 4),
                                            (np.uint8, 2), (np.uint8, 5), (np.float32, 1)])
def test_whole_pixel_gather_matches_per_channel_indexing(dtype, channels):
    rng = np.random.default_rng(5)
    img = (rng.random((20, 30, channels)) * 200).astype(dtype)
    rows, cols = rng.integers(0, 20, (7, 9)), rng.integers(0, 30, (7, 9))
    assert np.array_equal(P._gather(img, rows, cols), img[rows, cols])
