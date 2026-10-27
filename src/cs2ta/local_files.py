"""Local directory picker for large demos, without browser uploads."""

from pathlib import Path

import streamlit as st


def default_browse_directory() -> Path:
    home = Path.home()
    for path in [home / "Downloads/g2-vs-navi", home / "Downloads", home]:
        if path.is_dir():
            return path
    return home


def _navigate(directory: str) -> None:
    st.session_state["demo_browse_directory"] = directory


def _enter_folder(parent: str, widget_key: str) -> None:
    name = st.session_state.get(widget_key)
    if name:
        _navigate(str((Path(parent) / name).resolve()))


def demo_picker() -> str:
    mode = st.selectbox("打开方式", ["浏览文件", "输入路径"], key="demo_open_mode")
    if mode == "输入路径":
        return st.text_input("本地 Demo 文件路径", key="demo_manual_path",
                             placeholder="/home/fuyang/Downloads/g2-vs-navi/match.dem").strip()

    st.session_state.setdefault("demo_browse_directory", str(default_browse_directory()))
    directory = Path(st.session_state["demo_browse_directory"])
    home = Path.home()
    columns = st.columns(3)
    columns[0].button("主目录", on_click=_navigate, args=(str(home),), width="stretch")
    columns[1].button("下载", on_click=_navigate, args=(str(home / "Downloads"),),
                      disabled=not (home / "Downloads").is_dir(), width="stretch")
    columns[2].button("上一级", on_click=_navigate, args=(str(directory.parent),),
                      disabled=directory == directory.parent, width="stretch")
    st.caption(f"当前目录：{directory}")
    try:
        folders, demos = [], []
        for entry in directory.iterdir():
            try:
                if entry.is_dir() and not entry.name.startswith("."):
                    folders.append(entry.name)
                elif entry.is_file() and entry.suffix.lower() == ".dem":
                    demos.append(entry.name)
            except OSError:
                continue
        folders.sort(key=str.casefold)
        demos.sort(key=str.casefold)
    except OSError as error:
        st.error(f"无法浏览此目录：{error}。可以返回上一级或切换为输入路径。")
        return ""

    folder_key = f"demo_folder:{directory}"
    st.selectbox("进入子文件夹", folders, index=None, placeholder="选择文件夹进入",
                 key=folder_key, on_change=_enter_folder, args=(str(directory), folder_key),
                 disabled=not folders)
    if not demos:
        st.info("此目录没有 .dem 文件，请进入子文件夹或返回上一级。")
        return ""
    name = st.selectbox("选择 Demo 文件", demos, index=None, placeholder="选择比赛录像",
                        key=f"demo_file:{directory}")
    if not name:
        return ""
    path = directory / name
    try:
        st.caption(f"{path.stat().st_size / (1024 * 1024):.1f} MiB · {path}")
    except OSError as error:
        st.error(f"文件已不可用：{error}")
        return ""
    return str(path)
