from streamlit.testing.v1 import AppTest

from cs2ta import local_files


# Retain coverage for the optional local-picker component; the application uses browser upload.
PICKER_APP = '''
import streamlit as st
from cs2ta.local_files import demo_picker
demo = demo_picker()
st.button("打开比赛", disabled=not demo.strip(), key="parse_demo")
'''


def test_directory_navigation_does_not_reuse_a_file_from_another_directory(tmp_path, monkeypatch):
    (tmp_path / "first.dem").write_bytes(b"fixture")
    (tmp_path / "ignore.txt").write_text("not a demo")
    sub = tmp_path / "matches"
    sub.mkdir()
    (sub / "second.DEM").write_bytes(b"fixture")
    monkeypatch.setattr(local_files, "default_browse_directory", lambda: tmp_path)
    app = AppTest.from_string(PICKER_APP)
    app.run()
    assert not app.exception
    assert app.button(key="parse_demo").disabled
    files = app.selectbox(key=f"demo_file:{tmp_path}")
    assert files.options == ["first.dem"]
    files.select("first.dem").run()
    assert not app.button(key="parse_demo").disabled
    app.selectbox(key=f"demo_folder:{tmp_path}").select("matches").run()
    assert not app.exception
    assert app.session_state["demo_browse_directory"] == str(sub)
    assert app.button(key="parse_demo").disabled
    app.selectbox(key=f"demo_file:{sub}").select("second.DEM").run()
    assert not app.button(key="parse_demo").disabled


def test_empty_directory_and_manual_path(tmp_path, monkeypatch):
    monkeypatch.setattr(local_files, "default_browse_directory", lambda: tmp_path)
    app = AppTest.from_string(PICKER_APP)
    app.run()
    assert not app.exception
    assert app.button(key="parse_demo").disabled
    app.selectbox(key="demo_open_mode").select("输入路径").run()
    app.text_input(key="demo_manual_path").input(str(tmp_path / "match.dem")).run()
    assert not app.exception
    assert not app.button(key="parse_demo").disabled
