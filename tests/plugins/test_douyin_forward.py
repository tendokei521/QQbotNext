"""douyin_parser 合并转发节点构建单测（全离线）。"""

from app.plugins.douyin_parser import forward as fwd

VIDEO_META = {
    "aweme_id": "7534291258635816251",
    "desc": "测试文案 #话题",
    "content_type": "video",
    "author_nickname": "作者名",
    "cover_url": "https://p3.douyinpic.com/cover.jpeg",
    "duration": 12500,
    "url": "https://www.douyin.com/video/7534291258635816251",
}

ALBUM_META = {
    "aweme_id": "7000000000000000001",
    "desc": "图集文案",
    "content_type": "image",
    "author_nickname": "图集作者",
    "cover_url": None,
    "image_count": 3,
    "url": "https://www.douyin.com/video/7000000000000000001",
}


def _text_of(content) -> str:
    return "".join(seg["data"]["text"] for seg in content if seg["type"] == "text")


def test_file_uri_is_onebot_compatible():
    uri = fwd.file_uri(r"C:\Users\Admin\Desktop\a b\v.mp4")
    assert uri.startswith("file:///")
    assert "\\" not in uri
    assert uri.endswith("/a b/v.mp4")


def test_build_forward_nodes_with_video_has_two_separate_nodes():
    nodes = fwd.build_forward_nodes(VIDEO_META, uin="123456", video_file=r"C:\tmp\7534291258635816251.mp4")

    assert len(nodes) == 2
    assert all(node["type"] == "node" for node in nodes)
    assert all(node["data"]["uin"] == "123456" for node in nodes)
    assert all(node["data"]["name"] == fwd.FORWARD_NAME for node in nodes)

    intro = nodes[0]["data"]["content"]
    assert any(seg["type"] == "image" for seg in intro)  # 封面
    text = _text_of(intro)
    assert "测试文案" in text
    assert "作者名" in text
    assert "00:12" in text  # 时长 12500ms
    assert "https://www.douyin.com/video/7534291258635816251" in text
    assert all(seg["type"] != "reply" for seg in intro)

    video = nodes[1]["data"]["content"]
    assert len(video) == 1
    assert video[0]["type"] == "video"
    assert video[0]["data"]["file"].startswith("file:///")
    assert video[0]["data"]["file"].endswith("7534291258635816251.mp4")


def test_build_forward_nodes_with_album_puts_all_images_in_one_node():
    nodes = fwd.build_forward_nodes(
        ALBUM_META, uin="1", image_files=[r"C:\tmp\a.jpg", r"C:\tmp\b.webp", r"C:\tmp\c.jpg"]
    )

    assert len(nodes) == 2
    media = nodes[1]["data"]["content"]
    assert [seg["type"] for seg in media] == ["image", "image", "image"]
    assert media[1]["data"]["file"].endswith("b.webp")
    assert "图集：3 张" in _text_of(nodes[0]["data"]["content"])


def test_build_forward_nodes_falls_back_to_text_on_error():
    nodes = fwd.build_forward_nodes(VIDEO_META, uin="1", media_error="下载失败")

    assert len(nodes) == 2
    fallback = nodes[1]["data"]["content"]
    assert fallback[0]["type"] == "text"
    assert "媒体获取失败" in fallback[0]["data"]["text"]
    assert "下载失败" in fallback[0]["data"]["text"]
    assert "https://www.douyin.com/video/7534291258635816251" in fallback[0]["data"]["text"]


def test_build_forward_nodes_without_media_keeps_intro_only():
    assert len(fwd.build_forward_nodes(VIDEO_META, uin="1")) == 1


def test_show_cover_false_drops_image_segment():
    nodes = fwd.build_forward_nodes(VIDEO_META, uin="1", show_cover=False)
    assert all(seg["type"] != "image" for seg in nodes[0]["data"]["content"])


def test_fallback_uin_when_missing():
    nodes = fwd.build_forward_nodes(VIDEO_META)
    assert nodes[0]["data"]["uin"] == fwd.FALLBACK_UIN


def test_intro_truncates_long_desc_and_handles_missing_fields():
    meta = {"aweme_id": "1", "desc": "长" * 300, "content_type": "video"}
    text = _text_of(fwd.build_intro_segments(meta))
    assert "..." in text
    assert len(text) < 300
    assert "未知作者" in text
    assert "https://www.douyin.com/video/1" in text  # 缺 url 时用 aweme_id 拼


def test_intro_without_desc_shows_placeholder():
    text = _text_of(fwd.build_intro_segments({"aweme_id": "1", "content_type": "video"}))
    assert "无文案" in text
