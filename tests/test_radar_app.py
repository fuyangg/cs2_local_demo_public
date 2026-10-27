import json
from pathlib import Path

import polars as pl
from PIL import Image
from streamlit.testing.v1 import AppTest

from cs2ta import radar
from cs2ta.pipeline import position_cache_path


def test_nuke_floor_switch_filters_table_and_keeps_world_view(tmp_path, monkeypatch):
    image = tmp_path / "map.png"
    Image.new("RGB", (1024, 1024)).save(image)
    monkeypatch.setattr(radar, "radar_path", lambda *args, **kwargs: image)
    pl.DataFrame([{"tick": 0, "event_type": "round_start"},
                  {"tick": 32, "event_type": "round_end"}], strict=False).write_parquet(tmp_path / "events.parquet")
    pl.DataFrame([{"round_id": 1, "start_tick": 0, "freeze_end_tick": 0,
                   "end_tick": 32, "winner": 2, "warmup": False}], strict=False).write_parquet(tmp_path / "rounds.parquet")
    states = pl.DataFrame([
        {"tick": tick, "steamid": pid, "name": pid, "x": 0., "y": 0., "z": z,
         "health": 100, "team_num": team, "is_alive": True}
        for tick in [0, 16, 32]
        for pid, z, team in [("upper-player", -400., 2), ("lower-player", -600., 3)]
    ], strict=False)
    states.write_parquet(position_cache_path(tmp_path, 1, 16))
    manifest = {"header": {"map_name": "de_nuke"}, "warnings": [], "demo_name": "fixture.dem",
                "parser_version": "test", "demo_sha256": "fixture", "absent_requested_events": []}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30)
    app.session_state["dataset"] = (str(tmp_path), manifest, "fixture.dem")
    app.run()
    assert not app.exception and not app.error
    assert app.tabs[0].dataframe[-1].value["steamid"].to_list() == ["upper-player"]
    app.radio[1].set_value("lower").run()
    assert not app.exception and not app.error
    assert app.tabs[0].dataframe[-1].value["steamid"].to_list() == ["lower-player"]
    app.radio[0].set_value("世界坐标").run()
    assert not app.exception and not app.error
    assert len(app.tabs[0].dataframe[-1].value) == 2
