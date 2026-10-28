from io import BytesIO
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from cs2ta import uploads
from cs2ta.uploads import saved_upload


class Upload(BytesIO):
    def __init__(self, content, name="match.dem"):
        super().__init__(content)
        self.name = name
        self.size = len(content)

    def read(self, size=-1):
        assert 0 < size <= uploads.CHUNK_BYTES
        return super().read(size)


def test_upload_is_default_and_does_not_offer_server_path_browsing():
    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
    assert not app.exception
    assert len(app.get("file_uploader")) == 1
    assert app.button(key="parse_demo").label == "上传并分析"
    assert app.button(key="parse_demo").disabled
    assert not app.text_input
    assert not app.selectbox
    assert not any("当前目录" in caption.value for caption in app.caption)
    assert app.get("file_uploader")[0].proto.multiple_files


def test_append_batches_deduplicate_and_failures_keep_all_prior_matches(tmp_path, monkeypatch):
    from cs2ta import pipeline
    from test_backend import FixtureParser

    monkeypatch.setattr(pipeline, "DemoParser", FixtureParser)
    FixtureParser.broken = False
    matches = {}
    first = uploads.analyze_uploads([Upload(b"PBDEMS2\x00one", "same.dem"),
                                    Upload(b"PBDEMS2\x00two", "same.dem")], matches, tmp_path)
    assert len(matches) == 2
    ids = [item["match_id"] for item in first]
    assert len(set(ids)) == 2
    result = uploads.analyze_uploads([Upload(b"broken", "bad.dem"),
                                     Upload(b"PBDEMS2\x00three", "third.dem")], matches, tmp_path)
    assert result[0]["error"] and result[1]["match_id"]
    assert len(matches) == 3 and all(mid in matches for mid in ids)
    result = uploads.analyze_uploads([Upload(b"PBDEMS2\x00one", "renamed.dem")], matches, tmp_path)
    assert len(matches) == 3 and result[0]["match_id"] == ids[0]
    assert matches[ids[0]]["name"] == "renamed.dem"
    assert len(list((tmp_path / "uploads").glob("*.dem"))) == 3


def test_batch_limit_and_current_session_registry_are_independent(tmp_path, monkeypatch):
    from cs2ta import pipeline
    from test_backend import FixtureParser

    monkeypatch.setattr(pipeline, "DemoParser", FixtureParser)
    FixtureParser.broken = False
    one, another_session = {}, {}
    uploads.analyze_uploads([Upload(b"PBDEMS2\x00one")], one, tmp_path)
    assert len(one) == 1 and not another_session
    with pytest.raises(ValueError, match="1–50"):
        uploads.analyze_uploads([Upload(b"PBDEMS2\x00one")] * 51, one, tmp_path)
    assert len(one) == 1


def test_uploads_use_content_identity_and_never_filename_as_server_path(tmp_path):
    first = Upload(b"PBDEMS2\x00first", "../../match.dem")
    with saved_upload(first, tmp_path) as (source, name):
        assert name == "match.dem"
        assert source.parent == tmp_path / "uploads"
        assert source.name != name
        assert source.read_bytes() == b"PBDEMS2\x00first"
    assert first.tell() == 0
    with saved_upload(Upload(b"PBDEMS2\x00first", r"C:\private\renamed.dem"), tmp_path) as (repeated, name):
        assert repeated == source
        assert name == "renamed.dem"
    with saved_upload(Upload(b"PBDEMS2\x00second"), tmp_path) as (different, _):
        assert different != source
        assert source.read_bytes() == b"PBDEMS2\x00first"
    assert len(list((tmp_path / "uploads").glob("*.dem"))) == 2
    assert not list(tmp_path.rglob("*.part"))


@pytest.mark.parametrize("name,content", [("match.zip", b"PBDEMS2\x00valid"),
                                          ("match.dem", b"not a CS2 demo"), ("match.dem", b"")])
def test_invalid_extension_or_header_never_reaches_parser(tmp_path, name, content):
    with pytest.raises(ValueError):
        with saved_upload(Upload(content, name), tmp_path):
            pytest.fail("Invalid upload reached parsing")
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.dem"))


def test_size_is_checked_both_before_and_during_copy(tmp_path, monkeypatch):
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 10)
    for declared_size in (100, 0):
        upload = Upload(b"PBDEMS2\x00too big")
        upload.size = declared_size
        with pytest.raises(ValueError, match="不能超过"):
            with saved_upload(upload, tmp_path):
                pytest.fail("Oversized upload reached parsing")
    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("*.dem"))


def test_failed_parse_cleans_new_upload_but_keeps_existing_content(tmp_path):
    def failing_parse():
        with saved_upload(Upload(b"PBDEMS2\x00fixture"), tmp_path):
            raise ValueError("parser failed")

    with pytest.raises(ValueError, match="parser failed"):
        failing_parse()
    assert not list(tmp_path.rglob("*.dem"))
    with saved_upload(Upload(b"PBDEMS2\x00fixture"), tmp_path) as (source, _):
        pass
    with pytest.raises(ValueError, match="parser failed"):
        failing_parse()
    assert source.is_file()
    assert not list(tmp_path.rglob("*.part"))
