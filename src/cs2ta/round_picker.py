"""Friendly round filtering and a checkbox table with a strict 20-round cap."""

import polars as pl
import streamlit as st

from cs2ta.frames import column as frame_column
from cs2ta.round_context import filter_rounds
from cs2ta.economy import ECONOMY_LABELS
from cs2ta.comparison import comparison_teams, filter_comparison

MAX_ROUNDS = 20


def _set_selection(key, ids):
    if len(ids) > MAX_ROUNDS:
        st.session_state[f"{key}:error"] = "最多同时选择 20 个回合，请先取消一些回合。"
    else:
        st.session_state[key] = list(ids)
    st.session_state[f"{key}:version"] += 1


def _edit_selection(key, editor_key, ids):
    selected = set(st.session_state[key])
    for index, changes in st.session_state[editor_key].get("edited_rows", {}).items():
        if "播放" in changes:
            rid = ids[int(index)]
            if changes["播放"]:
                selected.add(rid)
            else:
                selected.discard(rid)
    _set_selection(key, sorted(selected))


def pick_rounds(rounds: pl.DataFrame, destination, default_round: int) -> list[int]:
    key = f"replay_selected:{destination}"
    st.session_state.setdefault(key, [default_round])
    st.session_state.setdefault(f"{key}:version", 0)
    allowed = set(rounds["round_id"].to_list())
    st.session_state[key] = [rid for rid in st.session_state[key] if rid in allowed]
    multiple = "match_id" in rounds.columns
    labels = comparison_teams(rounds) if multiple else {}
    for team in ([] if multiple else ["team_a", "team_b"]):
        column = f"{team}_name"
        names = frame_column(rounds, column).drop_nulls().cast(pl.String)
        labels[team] = names[-1] if len(names) else ("队伍 A" if team == "team_a" else "队伍 B")
    left, middle, right = st.columns(3)
    team_key = "comparison_filter_team" if multiple else "round_filter_team"
    if st.session_state.get(team_key) not in labels:
        st.session_state.pop(team_key, None)
    team = left.selectbox("观察哪支队伍", list(labels), format_func=lambda value: labels[value],
                          help="跨比赛按战队名称匹配；无名称的队伍按比赛分开。", key=team_key)
    side = middle.selectbox("进攻还是防守", ["all", "2", "3"],
                            format_func=lambda value: {"all": "都看", "2": "进攻（T）", "3": "防守（CT）"}[value],
                            key="round_filter_side")
    phase = right.selectbox("比赛阶段", ["all", "regulation", "overtime"],
                            format_func=lambda value: {"all": "全部阶段", "regulation": "常规回合", "overtime": "加时回合"}[value],
                            key="round_filter_phase")
    economy = st.selectbox("经济 / 配枪", list(ECONOMY_LABELS), format_func=ECONOMY_LABELS.get,
                           key="round_filter_economy",
                           help="按观察队伍冻结结束时全队人均装备价值估算：eco < $2,000；force-buy $2,000–3,999；4k-buy $4,000–4,999；full-buy ≥ $5,000。double-awp 是至少两把 AWP 的独立标签，可与购买档位重叠。")
    with st.expander("经济分类规则"):
        st.caption("按冻结结束后的完整五人快照分类，包含已持有或保下的装备，并非本回合花费或战术意图。手枪局需有半场开始/首局元数据、非预热非加时、五人开局资金不超过 $800 且至少三人持有手枪；不按第 13/16 回合猜测。缺少冻结结束、完整阵容或装备价值时标为未知，AWP 数据不足也不当作零把。")
    filtered = filter_comparison(rounds, team, side, phase, economy) if multiple else filter_rounds(rounds, team, side, phase, economy)
    ids = filtered["round_id"].cast(pl.Int64).to_list()
    buttons = st.columns(3)
    buttons[0].button("勾选筛选结果", on_click=_set_selection, args=(key, ids),
                       disabled=not ids or len(ids) > MAX_ROUNDS, width="stretch", key="select_filtered_rounds")
    buttons[1].button("只选前 20 个", on_click=_set_selection, args=(key, ids[:MAX_ROUNDS]),
                       disabled=len(ids) <= MAX_ROUNDS, width="stretch", key="select_first_twenty")
    buttons[2].button("清空选择", on_click=_set_selection, args=(key, []), width="stretch", key="clear_replay_rounds")
    error = st.session_state.pop(f"{key}:error", None)
    if error:
        st.warning(error)
    st.caption(f"已选 {len(st.session_state[key])}/20 回合 · 当前筛选匹配 {len(ids)} 回合。可直接勾选下表；一键勾选会替换当前选择。")
    if not ids:
        st.info("没有匹配回合。若数据缺失，请切回“都看 / 全部阶段 / 全部经济”手动选择。")
        return st.session_state[key]
    table_data = {"播放": [rid in st.session_state[key] for rid in ids],
                  "回合": filtered["source_round_id"].to_list() if multiple else ids}
    if multiple:
        table_data["比赛"] = filtered["match_name"].to_list()
        for role in ("team_a", "team_b"):
            table_data[f"{role[-1].upper()} 队名称"] = frame_column(filtered, f"{role}_name")
    for team_id, label in [("team_a", "A"), ("team_b", "B")]:
        values = frame_column(filtered, f"{team_id}_side")
        table_data[f"队伍{label}"] = ["T · 进攻" if value == 2 else "CT · 防守" if value == 3 else "未知" for value in values]
    table_data["阶段"] = ["未知" if value is None else "加时" if value else "常规" for value in frame_column(filtered, "is_overtime")]
    economy_columns = [("team_a", "A 队"), ("team_b", "B 队")] if multiple else [(team, "")]
    for role, prefix in economy_columns:
        kinds = frame_column(filtered, f"{role}_buy_type").cast(pl.String).fill_null("unknown")
        double = frame_column(filtered, f"{role}_double_awp").cast(pl.Boolean).fill_null(False)
        table_data[f"{prefix}经济"] = [ECONOMY_LABELS.get(kind, "经济未知") + (" · double-awp" if awp else "") for kind, awp in zip(kinds, double)]
        for field, label in [("equip_avg", "人均装备 $"), ("spent_avg", "人均本局花费 $"), ("awp_count", "AWP 数")]:
            table_data[f"{prefix}{label}"] = frame_column(filtered, f"{role}_{field}").cast(pl.Float64, strict=False)
    table = pl.DataFrame(table_data)
    editor_key = f"round_editor:{destination}:{team}:{side}:{phase}:{economy}:{st.session_state[f'{key}:version']}"
    st.data_editor(table, hide_index=True, disabled=[column for column in table.columns if column != "播放"],
                   column_config={"播放": st.column_config.CheckboxColumn("播放", width="small"),
                                  "回合": st.column_config.NumberColumn("录制回合", format="R%d"),
                                  "人均装备 $": st.column_config.NumberColumn(format="$%.0f"),
                                  "人均本局花费 $": st.column_config.NumberColumn(format="$%.0f"),
                                  "AWP 数": st.column_config.NumberColumn(format="%d")},
                   height=min(330, 38 + 35 * len(table)), width="stretch", key=editor_key,
                   on_change=_edit_selection, args=(key, editor_key, ids))
    if not multiple and f"{team}_buy_type" not in rounds.columns:
        st.caption("当前尚无经济缓存：重新打开比赛后会读取冻结结束的经济/配枪数据；未知回合仍可手动选择。")
    if any(column not in rounds.columns for column in ["team_a_side", "is_overtime"]):
        st.caption("当前为旧缓存：重新打开比赛可补全队伍与加时筛选信息。")
    return st.session_state[key]
