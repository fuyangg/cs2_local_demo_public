from pathlib import Path

import polars as pl
import pytest

from cs2ta import comparison, pipeline
from cs2ta.comparison import comparison_payload, comparison_rounds, comparison_teams, filter_comparison
from cs2ta.uploads import analyze_uploads
from test_backend import FixtureParser
from test_uploads import Upload


@pytest.fixture
def matches(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline, "DemoParser", FixtureParser)
    FixtureParser.broken = False
    catalog = {}
    analyze_uploads([Upload(b"PBDEMS2\x00first", "same.dem"),
                     Upload(b"PBDEMS2\x00second", "same.dem")], catalog, tmp_path)
    for match in catalog.values():
        pipeline.parse_rounds_positions(match["source"], Path(match["destination"]), [1], 16)
        pipeline.parse_utility_tracks(match["source"], Path(match["destination"]))
    return list(catalog.values())


def test_repeated_round_ids_and_player_ids_are_independent(matches):
    rows = comparison_rounds(matches)
    assert rows["round_id"].to_list() == [1, 2, 3, 4]
    assert rows["source_round_id"].to_list() == [1, 2, 1, 2]
    payload = comparison_payload(matches, [(match["id"], 1) for match in matches], 16, include_utility=True)
    assert len({row["id"] for row in payload["rounds"]}) == 2
    assert [row["sourceRoundId"] for row in payload["rounds"]] == [1, 1]
    assert len({player["id"] for player in payload["players"]}) == 20
    assert len({player["team"] for player in payload["players"]}) == 4
    for row in payload["rounds"]:
        assert row["utilities"][0]["player"] in {track["player"] for track in row["tracks"]}
    assert payload["rounds"][0]["label"] != payload["rounds"][1]["label"]


def test_named_team_filters_follow_a_b_changes_and_keep_unknowns_separate(matches):
    rounds = comparison_rounds(matches)
    assert filter_comparison(rounds, "name:G2", "2", "regulation", "full-buy").height == 4
    swapped = rounds.with_columns(
        pl.when(pl.col("match_id") == matches[1]["id"]).then(pl.lit("NAVI")).otherwise(pl.col("team_a_name")).alias("team_a_name"),
        pl.when(pl.col("match_id") == matches[1]["id"]).then(pl.lit("G2")).otherwise(pl.col("team_b_name")).alias("team_b_name"))
    selected = filter_comparison(swapped, "name:G2", "3", "all", "all")
    assert selected["match_id"].unique().to_list() == [matches[1]["id"]]
    unknown = rounds.with_columns(pl.lit("队伍 A").alias("team_a_name"), pl.lit(None).alias("team_b_name"))
    assert len(comparison_teams(unknown)) == 5


def test_refuse_mixed_maps_duplicate_selection_and_global_limits(matches, monkeypatch):
    with pytest.raises(ValueError, match="同一地图"):
        comparison_rounds([matches[0], {**matches[1], "map_name": "de_nuke"}])
    with pytest.raises(ValueError, match="同一地图"):
        comparison_rounds([{**matches[0], "map_name": ""}])
    with pytest.raises(ValueError, match="不同的比赛"):
        comparison_rounds([matches[0], matches[0]])
    one = (matches[0]["id"], 1)
    for selection in ([], [one, one], [(one[0], rid) for rid in range(21)]):
        with pytest.raises(ValueError, match="1–20"):
            comparison_payload(matches, selection, 16)
    monkeypatch.setattr(comparison, "MAX_PLAYBACK_ROWS", 99)
    with pytest.raises(ValueError, match="100 万"):
        comparison_payload(matches, [(match["id"], 1) for match in matches], 16)


def test_missing_cache_is_explicit_and_does_not_parse_on_read(matches):
    with pytest.raises(FileNotFoundError, match="尚未准备好"):
        comparison_payload(matches, [(matches[0]["id"], 2)], 16)


def test_page_switches_multiple_single_and_empty_selection_without_stale_playback(matches):
    from streamlit.testing.v1 import AppTest

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=30)
    app.session_state["uploaded_matches"] = {match["id"]: match for match in matches}
    app.session_state["replay_view"] = "世界坐标"
    app.run()
    assert not app.exception and not app.error
    assert len(app.multiselect(key="upload_matches:de_dust2").value) == 2
    app.button(key="select_filtered_rounds").click().run()
    app.button(key="load_replay").click().run()
    assert not app.exception and not app.error
    assert len(app.get("iframe")) == 1
    app.multiselect(key="upload_matches:de_dust2").set_value([matches[1]["id"]]).run()
    assert not app.exception
    assert app.session_state["dataset"][1]["demo_sha256"] == matches[1]["id"]
    app.multiselect(key="upload_matches:de_dust2").set_value([]).run()
    assert not app.exception and not app.get("iframe")
    assert len(app.session_state["uploaded_matches"]) == 2
