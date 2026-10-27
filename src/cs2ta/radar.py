"""Versioned CS2 radar assets and world-to-radar projection.

Transforms and floor ranges come from awpy-data release 2000935's
map_data.json. Radar images remain local assets, outside the Git codebase.
"""

import base64
from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path
import tempfile
import urllib.request
import zipfile

import polars as pl
from PIL import Image
import plotly.graph_objects as go

from cs2ta.frames import column, missing, nulls
from cs2ta.round_context import PLAYER_COLORS, color_index
from cs2ta.runtime import settings

ASSET_VERSION = "2000935"
ASSET_URL = f"https://github.com/pnxenopoulos/awpy-data/releases/download/{ASSET_VERSION}/images.zip"
ASSET_SHA256 = "217dec0b1f2203cc7e9f7bb1b9ef13873665f093203a7df7231b9eb0074b649e"
RADAR_SIZE = 1024
CACHE_ROOT = Path(".cache/maps")


@dataclass(frozen=True)
class Radar:
    name: str
    pos_x: float
    pos_y: float
    scale: float
    lower_max_z: float | None = None

    def project(self, x, y):
        # Radar PNG origin is upper-left; world Y points in the other direction.
        return (x - self.pos_x) / self.scale, (self.pos_y - y) / self.scale


RADARS = {
    "de_ancient": Radar("de_ancient", -2953.0, 2164.0, 5.0),
    "de_anubis": Radar("de_anubis", -2796.0, 3328.0, 5.22),
    "de_cache": Radar("de_cache", -2000.0, 3250.0, 5.5),
    "de_dust2": Radar("de_dust2", -2476.0, 3239.0, 4.4),
    "de_inferno": Radar("de_inferno", -2087.0, 3870.0, 4.9),
    "de_mirage": Radar("de_mirage", -3230.0, 1713.0, 5.0),
    "de_nuke": Radar("de_nuke", -3453.0, 2887.0, 7.0, lower_max_z=-495.0),
}
IMAGE_NAMES = [name for radar in RADARS.values()
               for name in ([f"{radar.name}.png", f"{radar.name}_lower.png"]
                            if radar.lower_max_z is not None else [f"{radar.name}.png"])]


def radar_path(radar: Radar, layer: str = "upper", cache_root: Path | None = None) -> Path:
    suffix = "_lower" if layer == "lower" and radar.lower_max_z is not None else ""
    return (cache_root or settings().map_cache_dir) / ASSET_VERSION / f"{radar.name}{suffix}.png"


def download_radars(cache_root: Path | None = None) -> list[Path]:
    """Fetch one pinned archive, verify checksum, then publish only known PNGs."""
    destination = (cache_root or settings().map_cache_dir) / ASSET_VERSION
    paths = [destination / name for name in IMAGE_NAMES]
    if all(path.is_file() for path in paths):
        return paths
    request = urllib.request.Request(ASSET_URL, headers={"User-Agent": "cs2-local-demo/0.1"})
    with urllib.request.urlopen(request, timeout=30) as response:
        archive = response.read(10 * 1024 * 1024 + 1)
    if len(archive) > 10 * 1024 * 1024:
        raise ValueError("地图资源超过预期大小，未保存。")
    if hashlib.sha256(archive).hexdigest() != ASSET_SHA256:
        raise ValueError("地图资源 SHA-256 不匹配，未保存。")
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive)) as bundle, tempfile.TemporaryDirectory(dir=destination) as tmp:
        for name in IMAGE_NAMES:
            # Explicit members only: no archive-controlled output paths.
            info = bundle.getinfo(f"radars/{name}")
            if info.file_size > 8 * 1024 * 1024:
                raise ValueError(f"地图图片过大：{name}")
            data = bundle.read(info)
            with Image.open(io.BytesIO(data)) as image:
                if image.format != "PNG" or image.size != (RADAR_SIZE, RADAR_SIZE):
                    raise ValueError(f"地图图片格式或尺寸不符合预期：{name}")
                image.verify()
            (Path(tmp) / name).write_bytes(data)
        for name in IMAGE_NAMES:
            os.replace(Path(tmp) / name, destination / name)
    return paths


def level_mask(states: pl.DataFrame, radar: Radar | None, layer: str = "upper") -> pl.Series:
    if radar is None or radar.lower_max_z is None:
        return pl.Series("visible", [True] * states.height, dtype=pl.Boolean)
    z = column(states, "z").cast(pl.Float64, strict=False).fill_nan(None)
    if layer == "lower":
        return ((z >= -10000) & (z < radar.lower_max_z)).fill_null(False)
    return ((z >= radar.lower_max_z) & (z < 10000)).fill_null(False)


def trail_coordinates(trail: pl.DataFrame, tick_step: int, radar: Radar | None, layer: str):
    """Preserve gaps, death intervals and off-floor samples as line breaks."""
    trail = nulls(trail).sort("tick")
    visible = (level_mask(trail, radar, layer) & (trail["is_alive"] == True)).fill_null(False)
    xs, ys, previous_tick = [], [], None
    for i, row in enumerate(trail.iter_rows(named=True)):
        if previous_tick is not None and row["tick"] - previous_tick > tick_step:
            xs.append(None)
            ys.append(None)
        previous_tick = row["tick"]
        if not visible[i] or missing(row["x"]) or missing(row["y"]):
            xs.append(None)
            ys.append(None)
            continue
        x, y = radar.project(row["x"], row["y"]) if radar else (row["x"], row["y"])
        xs.append(float(x))
        ys.append(float(y))
    return xs, ys


def position_figure(
    states: pl.DataFrame, snapshot: pl.DataFrame, tick: int,
    chosen: list[str], tick_step: int, radar: Radar | None = None,
    image_path: Path | None = None, layer: str = "upper",
) -> go.Figure:
    figure = go.Figure()
    states, snapshot = nulls(states), nulls(snapshot)
    if radar is not None:
        if image_path is None:
            raise ValueError("地图模式需要已下载的雷达底图。")
        image = "data:image/png;base64," + base64.b64encode(image_path.read_bytes()).decode("ascii")
        figure.add_layout_image(dict(
            source=image, xref="x", yref="y", x=0, y=0,
            sizex=RADAR_SIZE, sizey=RADAR_SIZE, xanchor="left", yanchor="top",
            sizing="stretch", opacity=1, layer="below",
        ))
    names = dict(states.unique("steamid", keep="last", maintain_order=True).select("steamid", "name").iter_rows())
    for pid in chosen:
        trail = states.filter((pl.col("steamid") == pid) & (pl.col("tick") <= tick))
        xs, ys = trail_coordinates(trail, tick_step, radar, layer)
        ids = [color_index(v) for v in column(trail, "player_color_id" if "player_color_id" in trail.columns else "player_color")]
        ids = [value for value in ids if value is not None]
        color = PLAYER_COLORS[ids[-1]] if ids else "#a7afba"
        sides = trail["team_num"].drop_nulls()
        dash = "dash" if len(sides) and sides[-1] == 2 else "solid"
        figure.add_trace(go.Scatter(x=xs, y=ys, mode="lines", connectgaps=False,
                                   line={"width": 2.5, "color": color, "dash": dash}, name=str(names.get(pid, pid))))
    snapshot = snapshot.filter(level_mask(snapshot, radar, layer))
    for team, label in [(2, "T"), (3, "CT")]:
        points = snapshot.filter(pl.col("team_num") == team).drop_nulls(["x", "y"])
        xs, ys = radar.project(points["x"], points["y"]) if radar else (points["x"], points["y"])
        ids = column(points, "player_color_id" if "player_color_id" in points.columns else "player_color")
        colors = [PLAYER_COLORS[index] if (index := color_index(value)) is not None else "#a7afba" for value in ids]
        figure.add_trace(go.Scatter(
            x=xs.to_list(), y=ys.to_list(), mode="markers+text", text=points["name"].to_list(), textposition="top center", name=label,
            customdata=points.select("x", "y", "z", "health").rows(),
            hovertemplate="%{text}<br>世界 X=%{customdata[0]}<br>世界 Y=%{customdata[1]}"
                          "<br>Z=%{customdata[2]}<br>HP=%{customdata[3]}<extra></extra>",
            marker={"size": 13, "color": colors, "line": {"width": 1, "color": "white"}},
        ))
    figure.update_layout(height=760, margin={"t": 20, "l": 10, "r": 10, "b": 10},
                         uirevision=f"{radar.name if radar else 'world'}-{layer}",
                         legend={"orientation": "h"})
    if radar:
        figure.update_layout(plot_bgcolor="#101510", paper_bgcolor="#101510", font_color="white")
        figure.update_xaxes(range=[0, RADAR_SIZE], visible=False, constrain="domain")
        figure.update_yaxes(range=[RADAR_SIZE, 0], visible=False, scaleanchor="x", scaleratio=1, constrain="domain")
    else:
        extent = states.drop_nulls(["x", "y"])
        if not extent.is_empty():
            figure.update_xaxes(range=[float(extent["x"].min()) - 100, float(extent["x"].max()) + 100])
            figure.update_yaxes(range=[float(extent["y"].min()) - 100, float(extent["y"].max()) + 100])
        figure.update_xaxes(title="世界坐标 X")
        figure.update_yaxes(title="世界坐标 Y", scaleanchor="x", scaleratio=1)
    return figure
