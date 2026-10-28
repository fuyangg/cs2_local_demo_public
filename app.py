"""Run from the repository root with: streamlit run app.py."""

from pathlib import Path

import polars as pl
import streamlit as st

from cs2ta.analysis import events_for_round, kill_summary
from cs2ta.frames import read_parquet
from cs2ta.pipeline import load_dataset, parse_round_positions, position_cache_path
from cs2ta.playback_ui import render_round_player, render_comparison_player
from cs2ta.radar import ASSET_VERSION, RADARS, download_radars, level_mask, position_figure, radar_path
from cs2ta.runtime import settings, version_label
from cs2ta.release import production_ready
from cs2ta.uploads import MAX_UPLOAD_MIB, MAX_UPLOAD_BATCH, analyze_uploads
from cs2ta.comparison import match_label

st.set_page_config(page_title="CS2 录像上传分析", page_icon="🎯", layout="wide")
runtime = settings()
if runtime.environment != "prod" and (Path.cwd() / ".runtime/deployment.json").is_file():
    st.error("这是稳定版目录，请使用 scripts/run-prod.sh 启动；开发请回到 dev 目录。")
    st.stop()
if runtime.environment == "prod":
    try:
        production_ready(Path.cwd())
    except (OSError, ValueError, KeyError) as error:
        st.error(f"稳定版启动检查未通过：{error}")
        st.stop()
st.title("CS2 回合回放")
st.caption("上传多份录像，由服务器分析后选择同地图比赛，叠加比较回合轨迹。")
matches = st.session_state.setdefault("uploaded_matches", {})
# Preserve an already analyzed single match when upgrading an active session.
if not matches and "dataset" in st.session_state:
    old_destination, old_manifest, old_source = st.session_state["dataset"]
    mid = old_manifest["demo_sha256"]
    matches[mid] = {"id": mid, "name": old_manifest["demo_name"], "source": old_source,
                    "destination": old_destination, "manifest": old_manifest,
                    "map_name": str(old_manifest["header"].get("map_name") or "").strip().lower()}
selected_matches = []

with st.sidebar:
    if runtime.environment == "prod":
        st.success("稳定版 · PROD")
    else:
        st.info("开发预览 · DEV")
    st.caption(version_label())
    st.header("1. 上传比赛录像")
    files = st.file_uploader("选择或拖入 CS2 录像（可多选）", type=None, accept_multiple_files=True, key="demo_upload",
                              help=f"上传已解压的 .dem 文件，单个文件最多 {MAX_UPLOAD_MIB} MiB。iPhone 请从“文件”中选择录像；其他格式不能分析。")
    st.caption("录像会上传到运行本应用的服务器进行分析，无需填写文件路径。")
    parse_clicked = st.button("上传并分析", type="primary", disabled=not files or len(files) > MAX_UPLOAD_BATCH,
                              key="parse_demo")
    st.caption(f"每批最多 {MAX_UPLOAD_BATCH} 份录像，逐份分析。追加上传不会顶替已分析的比赛；相同内容复用缓存。")
    if len(files) > MAX_UPLOAD_BATCH:
        st.warning(f"请将每批录像减少到 {MAX_UPLOAD_BATCH} 份以内。")
    if parse_clicked:
        with st.status("正在分析本批录像……", expanded=True) as status:
            progress = st.progress(0, text="准备上传分析……")
            results = analyze_uploads(files, matches, runtime.data_dir,
                lambda index, total, name: progress.progress(index / total,
                    text=f"已完成 {index}/{total} · {name}"))
            st.session_state["upload_results"] = results
            succeeded = [row["match_id"] for row in results if row["match_id"]]
            progress.empty()
            status.update(label=f"本批次：{len(succeeded)} 成功 / {len(results) - len(succeeded)} 失败",
                          state="complete" if succeeded else "error", expanded=False)
            if succeeded:
                map_name = matches[succeeded[-1]]["map_name"]
                st.session_state["upload_map"] = map_name
                key = f"upload_matches:{map_name}"
                previous = st.session_state.get(key, [mid for mid, item in matches.items() if item["map_name"] == map_name])
                chosen = list(dict.fromkeys(previous + [mid for mid in succeeded if matches[mid]["map_name"] == map_name]))
                st.session_state[key] = chosen if map_name else chosen[:1]
    if "upload_results" in st.session_state:
        with st.expander("最近一批的逐文件结果", expanded=any(row["error"] for row in st.session_state["upload_results"])):
            for row in st.session_state["upload_results"]:
                if row["error"]:
                    st.error(f"{row['name']} · 上传分析失败：{row['error']}")
                else:
                    st.write(f"✓ {row['name']} · {matches[row['match_id']]['map_name'] or '地图未知'}")
    if matches:
        st.header("2. 选择分析比赛")
        st.caption(f"本次会话已保留 {len(matches)} 场比赛。移除待上传文件不会删除已分析的比赛。")
        maps = sorted({item["map_name"] for item in matches.values()})
        if st.session_state.get("upload_map") not in maps:
            st.session_state.pop("upload_map", None)
        map_name = st.selectbox("地图", maps, key="upload_map",
            format_func=lambda value: value or "地图未知（仅支持单场查看）")
        available = {mid: item for mid, item in matches.items() if item["map_name"] == map_name}
        key = f"upload_matches:{map_name}"
        if key in st.session_state:
            retained = [mid for mid in st.session_state[key] if mid in available]
            st.session_state[key] = retained if map_name else retained[:1]
        defaults = {} if key in st.session_state else {"default": list(available) if map_name else list(available)[:1]}
        ids = st.multiselect("参与回放的比赛", list(available), key=key,
            format_func=lambda mid: match_label(available[mid]), max_selections=1 if not map_name else None, **defaults)
        selected_matches = [available[mid] for mid in ids]
        st.caption("同地图可以合并选择回合，合计最多 20 个；只选一场可查看比赛详情。")
        if len(selected_matches) == 1:
            item = selected_matches[0]
            st.session_state["dataset"] = (item["destination"], item["manifest"], item["source"])
        elif not selected_matches:
            st.info("请选择至少一场比赛。")
            st.stop()

if len(selected_matches) > 1:
    st.subheader(selected_matches[0]["map_name"])
    st.caption(f"已选择 {len(selected_matches)} 场比赛；回合、玩家和道具按比赛分别标识。")
    try:
        render_comparison_player(selected_matches)
    except Exception as error:
        st.error(f"无法读取对比比赛：{error}")
    st.stop()

if "dataset" not in st.session_state:
    st.info("在左侧选择或拖入 .dem 录像，再点击「上传并分析」。")
    st.markdown("当前版本提供比赛概览、完整回合列表、击杀与道具事件，以及按回合提取的二维玩家轨迹。")
    st.stop()

destination_text, manifest, loaded_demo = st.session_state["dataset"]
destination = Path(destination_text)
try:
    events, rounds = load_dataset(destination)
except Exception as error:
    st.error(f"无法读取缓存：{error}。请重新解析比赛。")
    st.stop()

st.subheader(manifest["header"].get("map_name", "未知地图"))
st.caption(f"已载入：{manifest['demo_name']}")

with st.expander("数据范围与已知限制"):
    for warning in manifest["warnings"]:
        st.write(f"• {warning}")
    st.write("该版本不识别战术标签、不估计区域控制；轨迹不是完整游戏回放。")
    st.write("录制中没有完整起止事件的回合不进入列表。预热元数据未知的回合仍保留。")
    st.json({"parser_version": manifest["parser_version"], "demo_sha256": manifest["demo_sha256"],
             "absent_requested_events": manifest["absent_requested_events"]})

round_tab, overview_tab = st.tabs(["回合回放", "比赛概览"])
with overview_tab:
    c1, c2, c3 = st.columns(3)
    c1.metric("完整回合（含可能的预热/刀局）", len(rounds))
    c2.metric("击杀事件", events.filter(pl.col("event_type") == "player_death").height)
    c3.metric("已提取事件", len(events))
    st.subheader("击杀事件统计")
    st.caption("按 Steam ID 聚合，排除世界击杀和自杀，包含可能的队友击杀；不等同于官方计分板。")
    st.dataframe(kill_summary(events), hide_index=True, width="stretch")
    st.subheader("回合索引")
    st.dataframe(rounds, hide_index=True, width="stretch")

with round_tab:
    hide_warmup = st.checkbox("隐藏已确认的预热回合", value=True)
    visible = rounds.filter(~pl.col("warmup").fill_null(False)) if hide_warmup else rounds
    if visible.is_empty():
        st.info("没有可显示回合，请取消隐藏预热。")
        st.stop()
    round_id = int(visible["round_id"][0])
    row = rounds.filter(pl.col("round_id") == round_id).row(0, named=True)
    round_events = events_for_round(events, row)
    st.subheader("2. 选择回合并播放")
    render_round_player(destination, loaded_demo, visible, round_id, manifest["header"].get("map_name", ""))

    with st.expander("查看事件详情（可选）"):
        round_id = st.selectbox("详细检查某一回合（不影响回放选择）", visible["round_id"].to_list())
        row = rounds.filter(pl.col("round_id") == round_id).row(0, named=True)
        round_events = events_for_round(events, row)
        st.subheader("回合事件")
        types = st.multiselect("筛选事件类型", sorted(round_events["event_type"].unique().to_list()),
                               default=sorted(round_events["event_type"].unique().to_list()))
        selected_events = round_events.filter(pl.col("event_type").is_in(types))
        preferred = ["tick", "event_type", "attacker_name", "user_name", "weapon", "headshot", "x", "y", "z", "event_id"]
        columns = [c for c in preferred if c in selected_events.columns]
        st.dataframe(selected_events.select(columns), hide_index=True, width="stretch")
        st.download_button("导出本回合事件 JSON", round_events.write_json(),
                           file_name=f"round-{round_id}-events.json", mime="application/json")

    with st.expander("静态快照与原始位置检查"):
        st.subheader("二维位置与轨迹")
        map_name = manifest["header"].get("map_name", "")
        radar = RADARS.get(map_name)
        layer = "upper"
        image_path = None
        if radar is not None:
            view = st.radio("显示方式", ["地图雷达", "世界坐标"], horizontal=True)
            if view == "地图雷达":
                if radar.lower_max_z is not None:
                    layer = st.radio("地图楼层", ["upper", "lower"],
                                     format_func=lambda value: "上层 / A 点" if value == "upper" else "下层 / B 点",
                                     horizontal=True)
                    st.caption("按官方雷达高度区间筛选；楼梯、管道中的高度分层是近似表示。跨层路径会断开。")
                image_path = radar_path(radar, layer)
                if not image_path.is_file():
                    st.info("首次需要下载地图资源（约 2.9 MB），保存在本地 .cache/maps/；后续可离线使用。")
                    if st.button("下载地图底图"):
                        try:
                            with st.spinner("下载并校验雷达底图……"):
                                download_radars()
                        except Exception as error:
                            st.error(f"地图下载失败：{error}。可切换为世界坐标继续分析，或按 README 手动下载。")
                st.caption(f"地图资源：awpy-data / CS2 {ASSET_VERSION}。旧版 Demo 的地图细节可能与底图存在差异。")
            else:
                radar = None
        else:
            supported = "、".join(name.removeprefix("de_").title() for name in RADARS)
            st.info(f"暂未配置 {map_name} 的地图资源，显示世界坐标。当前支持 {supported}。")
        st.caption("按原始 Tick 采样；阵营来自该时刻的 team_num。悬停可查看原始世界坐标和高度。")
        tick_step = st.select_slider("轨迹采样 Tick 间隔（越小越细）", options=[4, 8, 16, 32, 64], value=16)
        cache = position_cache_path(destination, round_id, tick_step)
        if st.button("提取该回合轨迹", type="primary"):
            try:
                with st.spinner("解析该回合的玩家状态……"):
                    parse_round_positions(loaded_demo, destination, round_id, tick_step)
            except Exception as error:
                st.error(f"位置解析失败：{error}")

        if cache.exists():
            try:
                states = read_parquet(cache)
                # Do not join last-known positions across ticks: missing players stay absent.
                ticks = sorted(states["tick"].unique().to_list())
                tick = st.select_slider("查看采样 Tick", options=ticks, value=ticks[0]) if len(ticks) > 1 else ticks[0]
                snapshot = states.filter(pl.col("tick") == tick)
                st.caption(f"当前采样 Tick {tick}：{len(snapshot)} 条玩家状态，"
                           f"{int(snapshot['is_alive'].sum())} 人存活；缺失状态不自动补齐。")
                live_only = st.checkbox("仅显示存活玩家", value=True)
                if live_only:
                    snapshot = snapshot.filter(pl.col("is_alive") == True)
                player_ids = states["steamid"].drop_nulls().unique().to_list()
                player_names = dict(states.unique("steamid", keep="last", maintain_order=True).select("steamid", "name").iter_rows())
                chosen = st.multiselect("显示移动轨迹的玩家", player_ids,
                                        format_func=lambda pid: f"{player_names.get(pid, '未知')} ({pid})")
                snapshot = snapshot.filter(level_mask(snapshot, radar, layer))
                if radar is None or (image_path is not None and image_path.is_file()):
                    fig = position_figure(states, snapshot, tick, chosen, tick_step, radar, image_path, layer)
                    st.plotly_chart(fig, width="stretch")
                    if radar is not None:
                        px, py = radar.project(snapshot["x"], snapshot["y"])
                        outside = (~(px.is_between(0, 1024) & py.is_between(0, 1024))).fill_null(True).sum()
                        if outside:
                            st.warning(f"当前有 {outside} 条状态缺少有效坐标或位于底图范围外，请检查采样或地图版本。")
                else:
                    st.info("下载底图后即可叠加轨迹，也可选择「世界坐标」。")
                st.caption(f"当前视图包含 {len(snapshot)} 条玩家状态。")
                if radar is not None and radar.lower_max_z is not None:
                    st.caption("其他楼层或缺失高度的状态不投影到本层。")
                st.dataframe(snapshot, hide_index=True, width="stretch")
            except Exception as error:
                st.error(f"轨迹显示失败：{error}")
        else:
            st.info("点击「提取该回合轨迹」后，可拖动 Tick 查看站位。首次仅解析事件，避免一次载入整场高频位置。")
