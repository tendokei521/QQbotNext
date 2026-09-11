"""bilibili_parser 合并转发节点构建单测（全离线）。"""

from module.modules.bilibili_parser import forward as fwd

INFO = {
    "bvid": "BV1zq836rEbk",
    "title": "测试视频标题",
    "pic": "https://i0.hdslb.com/x.jpg",
    "desc": "简介内容",
    "owner": {"name": "UP主"},
    "stat": {"view": 100, "like": 1, "favorite": 2, "coin": 3, "share": 4, "danmaku": 5},
}


def test_file_uri_is_onebot_compatible():
    uri = fwd.file_uri(r"C:\Users\Admin\Desktop\a b\v.mp4")
    assert uri.startswith("file:///")
    assert "\\" not in uri
    assert uri.endswith("/a b/v.mp4")


def test_build_forward_nodes_with_video_has_two_separate_nodes():
    nodes = fwd.build_forward_nodes(INFO, uin="123456", video_file=r"C:\tmp\BV1zq836rEbk_p1.mp4")

    assert len(nodes) == 2
    assert all(node["type"] == "node" for node in nodes)
    assert all(node["data"]["uin"] == "123456" for node in nodes)
    assert all(node["data"]["name"] == fwd.FORWARD_NAME for node in nodes)

    # 节点1：简介（标题 + 链接），且不含 reply 段
    intro = nodes[0]["data"]["content"]
    assert any(seg["type"] == "image" for seg in intro)  # 封面
    text = "".join(seg["data"]["text"] for seg in intro if seg["type"] == "text")
    assert "测试视频标题" in text
    assert "https://www.bilibili.com/video/BV1zq836rEbk" in text
    assert all(seg["type"] != "reply" for seg in intro)

    # 节点2：视频
    video = nodes[1]["data"]["content"]
    assert len(video) == 1
    assert video[0]["type"] == "video"
    assert video[0]["data"]["file"].endswith("BV1zq836rEbk_p1.mp4")
    assert video[0]["data"]["file"].startswith("file:///")


def test_build_forward_nodes_falls_back_to_text_on_error():
    nodes = fwd.build_forward_nodes(INFO, uin="1", video_error="取流失败（B站风控或该视频受限）")

    assert len(nodes) == 2
    fallback = nodes[1]["data"]["content"]
    assert fallback[0]["type"] == "text"
    assert "720P 视频获取失败" in fallback[0]["data"]["text"]
    assert "取流失败" in fallback[0]["data"]["text"]
    assert "https://www.bilibili.com/video/BV1zq836rEbk" in fallback[0]["data"]["text"]


def test_build_forward_nodes_without_video_keeps_intro_only():
    assert len(fwd.build_forward_nodes(INFO, uin="1")) == 1


def test_show_cover_false_drops_image_segment():
    nodes = fwd.build_forward_nodes(INFO, uin="1", show_cover=False)
    assert all(seg["type"] != "image" for seg in nodes[0]["data"]["content"])


def test_fallback_uin_when_missing():
    nodes = fwd.build_forward_nodes(INFO)
    assert nodes[0]["data"]["uin"] == fwd.FALLBACK_UIN
