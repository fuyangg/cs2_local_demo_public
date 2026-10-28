"""Streamlit prepares data once; the embedded browser owns playback interaction."""

from pathlib import Path
import hashlib

import polars as pl
import streamlit as st
import streamlit.components.v1 as components

from cs2ta import radar
from cs2ta.frames import read_parquet
from cs2ta.pipeline import parse_rounds_positions, position_cache_path, parse_utility_tracks
from cs2ta.playback import playback_html, playback_payload
from cs2ta.round_picker import pick_rounds
from cs2ta.comparison import comparison_payload, comparison_rounds, match_label, same_map


def render_round_player(destination: Path, demo: str, rounds: pl.DataFrame,
                        default_round: int, map_name: str, *, matches=None) -> None:
    selected = pick_rounds(rounds, destination, default_round)
    with st.expander("高级设置（通常保持默认即可）"):
        a, b, c = st.columns(3)
        anchor = a.selectbox("回合对齐", ["freeze_end", "round_start"],
                            format_func=lambda value: "从回合开始" if value == "round_start" else "从冻结结束",
                            key="replay_alignment")
        quality = b.selectbox("画面精度", ["自动（推荐）", "流畅（适合多回合）", "精细"], key="replay_quality")
        step = (32 if len(selected) > 8 else 16) if quality == "自动（推荐）" else {"流畅（适合多回合）": 32, "精细": 4}[quality]
        view = c.selectbox("地图显示", ["地图雷达", "世界坐标"], key="replay_view")
    spec = radar.RADARS.get(map_name) if view == "地图雷达" else None
    images = {}
    if spec:
        layers = ["upper", "lower"] if spec.lower_max_z is not None else ["upper"]
        images = {layer: radar.radar_path(spec, layer) for layer in layers}
        if any(not path.is_file() for path in images.values()):
            if st.button("下载回放地图底图", key="replay_download_maps"):
                try:
                    with st.spinner("下载并校验地图……"):
                        radar.download_radars()
                except Exception as error:
                    st.error(f"地图下载失败：{error}")
            if any(not path.is_file() for path in images.values()):
                st.info("请先下载底图，或切换到世界坐标。")
                return
    elif view == "地图雷达":
        st.info("此地图尚未配置雷达，使用世界坐标回放。")
    if not selected:
        st.info("请选择至少一个回合。")
        return
    show_utility = st.checkbox("加载道具轨迹与落点", value=True, key="replay_utility")
    if matches is not None:
        _render_comparison_replay(matches, rounds, selected, step, spec, images, anchor, show_utility)
        return
    paths = {rid: position_cache_path(destination, rid, step) for rid in selected}
    if st.button(f"查看这 {len(selected)} 个回合", type="primary", key="load_replay"):
        try:
            with st.spinner(f"准备 {len(selected)} 个回合的位置，首次需解析，已有缓存会复用……"):
                parse_rounds_positions(demo, destination, selected, step)
                if show_utility:
                    parse_utility_tracks(demo, destination)
        except Exception as error:
            st.error(f"回放数据加载失败：{error}")
    missing = [rid for rid, path in paths.items() if not path.is_file()]
    if missing:
        st.info(f"回合 {missing} 尚未准备好，请点击上方加载按钮。")
        return
    try:
        states = {rid: read_parquet(path) for rid, path in paths.items()}
        utility_path = destination / "utility-tracks-v1.parquet"
        utility = read_parquet(utility_path) if show_utility and utility_path.is_file() else None
        if show_utility and utility is None:
            st.info("点击上方加载按钮，解析道具飞行轨迹；当前仍可查看已有爆开事件。")
        events = read_parquet(destination / "events.parquet") if show_utility else None
        payload = playback_payload(states, rounds, step, spec, images, anchor,
                                   utility_tracks=utility, events=events)
        if any(item["fallback"] for item in payload["rounds"]):
            st.warning("部分回合缺少冻结结束 Tick，已使用回合开始；对应回合在播放器内标有提示。")
        st.caption("3. 点击播放，或拖动时间轴查看。地图可调大小、缩放和全屏；点击玩家旁的“单看”可聚焦一人。")
        components.html(playback_html(payload), height=1160, scrolling=True)
    except Exception as error:
        st.error(f"无法构建回放：{error}")


def render_comparison_player(matches):
    map_name = same_map(matches)
    rounds = comparison_rounds(matches)
    if st.checkbox("隐藏已确认的预热回合", value=True):
        rounds = rounds.filter(~pl.col("warmup").fill_null(False))
    if rounds.is_empty():
        st.info("没有可显示回合，请取消隐藏预热。")
        return
    key = hashlib.sha256("\n".join(match["id"] for match in matches).encode()).hexdigest()
    render_round_player(Path("comparison-" + key), "", rounds, int(rounds["round_id"][0]), map_name, matches=matches)


def _render_comparison_replay(matches, rounds, selected, step, spec, images, anchor, show_utility):
    chosen = rounds.filter(pl.col("round_id").is_in(selected))
    selection = [(row["match_id"], row["source_round_id"]) for row in chosen.iter_rows(named=True)]
    if st.button(f"查看这 {len(selection)} 个回合", type="primary", key="load_replay"):
        active = [match for match in matches if any(mid == match["id"] for mid, _ in selection)]
        progress = st.progress(0, text="准备跨比赛回放……")
        for index, match in enumerate(active):
            ids = [rid for mid, rid in selection if mid == match["id"]]
            destination = Path(match["destination"])
            progress.progress(index / len(active), text=f"{index + 1}/{len(active)} · {match_label(match)}")
            try:
                if any(not position_cache_path(destination, rid, step).is_file() for rid in ids):
                    parse_rounds_positions(match["source"], destination, ids, step)
                if show_utility and not (destination / "utility-tracks-v1.parquet").is_file():
                    parse_utility_tracks(match["source"], destination)
            except Exception as error:
                st.error(f"{match_label(match)}：{error}")
        progress.empty()
    try:
        payload = comparison_payload(matches, selection, step, spec, images, anchor, show_utility)
        if any(row["fallback"] for row in payload["rounds"]):
            st.warning("部分回合缺少冻结结束 Tick，已标注回退到回合开始。")
        st.caption("播放或拖动时间轴比较同地图比赛；回合、战队和玩家按比赛独立选择。")
        components.html(playback_html(payload), height=1160, scrolling=True)
    except FileNotFoundError as error:
        st.info(f"{error} 点击上方加载按钮准备回放。")
    except Exception as error:
        st.error(f"无法构建跨比赛回放：{error}")
