from polars.testing import assert_frame_equal
import hashlib
import io
import zipfile

import polars as pl
from PIL import Image
import pytest

from cs2ta import radar


def test_world_to_radar_origin_and_orientation():
    dust2 = radar.RADARS["de_dust2"]
    assert dust2.project(-2476, 3239) == (0, 0)
    # A point one radar-width east and south is the bottom-right corner.
    x, y = dust2.project(-2476 + 1024 * 4.4, 3239 - 1024 * 4.4)
    assert (x, y) == pytest.approx((1024, 1024))
    # Known approximate Dust2 T spawn projects near the bottom of the radar.
    x, y = dust2.project(0, -800)
    assert 500 < x < 650 and 850 < y < 1000


def test_nuke_floor_boundary_and_unknown_height():
    states = pl.DataFrame({"z": pl.Series([-600, -495, -400, None], dtype=pl.Float64)}, strict=False)
    nuke = radar.RADARS["de_nuke"]
    assert radar.level_mask(states, nuke, "lower").to_list() == [True, False, False, False]
    assert radar.level_mask(states, nuke, "upper").to_list() == [False, True, True, False]
    assert radar.radar_path(nuke, "lower").name == "de_nuke_lower.png"


def test_trails_break_at_missing_ticks_death_and_floor_changes():
    states = pl.DataFrame({
        "tick": [0, 16, 32, 48, 80, 96, 112],
        "x": [0.] * 7, "y": [0.] * 7,
        "z": [-400, -600, -400, -400, -400, -400, -400],
        "is_alive": [True, True, True, True, True, False, True],
    }, strict=False)
    xs, ys = radar.trail_coordinates(states, 16, radar.RADARS["de_nuke"], "upper")
    assert xs[1] is None  # Off-floor sample remains a line break.
    assert xs[4] is None and xs[5] is not None  # Sampling gap before Tick 80.
    assert xs[6] is None  # Dead sample remains a line break.
    assert len(xs) == len(ys) == 8


def test_radar_figure_embeds_image_and_reverses_y_without_changing_facts(tmp_path):
    image = tmp_path / "radar.png"
    Image.new("RGB", (1024, 1024)).save(image)
    states = pl.DataFrame([{
        "tick": 0, "steamid": "123", "name": "player", "x": -2476., "y": 3239.,
        "z": 0., "health": 100, "team_num": 2, "is_alive": True,
    }], strict=False)
    original = states.clone()
    fig = radar.position_figure(states, states, 0, ["123"], 16, radar.RADARS["de_dust2"], image)
    assert fig.layout.images[0].source.startswith("data:image/png;base64,")
    assert fig.layout.images[0]["x"] == fig.layout.images[0]["y"] == 0
    assert tuple(fig.layout.xaxis.range) == (0, 1024)
    assert tuple(fig.layout.yaxis.range) == (1024, 0)
    assert list(fig.data[1]["x"]) == [0]
    assert list(fig.data[1]["y"]) == [0]
    assert_frame_equal(states, original)


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_download_extracts_only_expected_images_and_reuses_cache(tmp_path, monkeypatch, legacy_cache):
    png = io.BytesIO()
    Image.new("RGB", (1024, 1024)).save(png, format="PNG")
    existing = {}
    if legacy_cache:
        destination = tmp_path / radar.ASSET_VERSION
        destination.mkdir()
        for name in ["de_dust2.png", "de_mirage.png", "de_nuke.png", "de_nuke_lower.png"]:
            path = destination / name
            path.write_bytes(png.getvalue())
            existing[path] = path.read_bytes()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        for name in radar.IMAGE_NAMES:
            bundle.writestr(f"radars/{name}", png.getvalue())
        bundle.writestr("../unexpected-file", b"do not extract")
    data = archive.getvalue()
    monkeypatch.setattr(radar, "ASSET_SHA256", hashlib.sha256(data).hexdigest())
    requests = []

    def fetch(request, timeout):
        requests.append(request.full_url)
        return io.BytesIO(data)

    monkeypatch.setattr(radar.urllib.request, "urlopen", fetch)
    paths = radar.download_radars(tmp_path)
    assert len(paths) == 8 and all(p.is_file() for p in paths)
    assert not (tmp_path / "unexpected-file").exists()
    assert radar.download_radars(tmp_path) == paths
    assert requests == [radar.ASSET_URL]
    assert all(path.read_bytes() == data for path, data in existing.items())


def test_download_rejects_wrong_checksum_without_publishing(tmp_path, monkeypatch):
    monkeypatch.setattr(radar.urllib.request, "urlopen", lambda *a, **kw: io.BytesIO(b"broken archive"))
    with pytest.raises(ValueError, match="SHA-256"):
        radar.download_radars(tmp_path)
    assert not list(tmp_path.rglob("*.png"))


@pytest.mark.parametrize(("name", "origin", "scale"), [
    ("de_ancient", (-2953., 2164.), 5.),
    ("de_anubis", (-2796., 3328.), 5.22),
    ("de_cache", (-2000., 3250.), 5.5),
    ("de_inferno", (-2087., 3870.), 4.9),
])
def test_added_active_duty_maps_use_matching_overview_coordinates(name, origin, scale):
    spec = radar.RADARS[name]
    assert spec.project(*origin) == (0., 0.)
    assert spec.project(origin[0] + 1024 * scale, origin[1] - 1024 * scale) == pytest.approx((1024., 1024.))
    assert radar.radar_path(spec).name == f"{name}.png"


def test_complete_active_duty_pool_and_floor_images():
    assert set(radar.RADARS) == {
        "de_ancient", "de_anubis", "de_cache", "de_dust2", "de_inferno", "de_mirage", "de_nuke",
    }
    assert set(radar.IMAGE_NAMES) == {f"{name}.png" for name in radar.RADARS} | {"de_nuke_lower.png"}
