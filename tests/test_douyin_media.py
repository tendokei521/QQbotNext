"""douyin_parser 的链接/媒体整理单测（纯逻辑，全离线）。

覆盖：分享文案与小程序卡片取链、aweme_id/sec_uid 提取、内容类型判定、
无水印地址整理（bit_rate / play_addr / uri 三级回退）、图集（含 Live 图）整理、
元信息与缓存命名。
"""

from module.modules.douyin_parser import media

# ---------------- 夹具：贴近真实接口返回结构 ----------------

DETAIL_VIDEO = {
    "aweme_detail": {
        "aweme_id": "7534291258635816251",
        "desc": "测试文案 #话题",
        "create_time": 1750000000,
        "aweme_type": 0,
        "author": {"nickname": "作者名", "sec_uid": "MS4wLjABAAAA_test"},
        "video": {
            "duration": 12500,
            "cover": {"url_list": ["https://p3.douyinpic.com/cover.jpeg"]},
            "play_addr": {
                "uri": "v0300fg10000abcdef",
                "url_list": ["https://aweme.snssdk.com/aweme/v1/playwm/?video_id=v0300fg10000abcdef&ratio=720p"],
            },
            "bit_rate": [
                {
                    "bit_rate": 800_000,
                    "gear_name": "normal_540_0",
                    "quality_type": {"name": "540p"},
                    "play_addr": {"url_list": ["https://cdn.example/540.mp4"]},
                },
                {
                    "bit_rate": 2_500_000,
                    "gear_name": "normal_1080_0",
                    "quality_type": {"name": "1080p"},
                    "play_addr": {"url_list": ["https://cdn.example/1080.mp4"]},
                },
                {
                    "bit_rate": 2_500_000,
                    "gear_name": "dup_1080",
                    "quality_type": {"name": "1080p"},
                    "play_addr": {"url_list": ["https://cdn.example/1080.mp4"]},  # 重复 URL
                },
            ],
        },
    }
}

DETAIL_ALBUM = {
    "aweme_detail": {
        "aweme_id": "7000000000000000001",
        "desc": "图集文案",
        "aweme_type": 68,
        "author": {"nickname": "图集作者", "sec_uid": "MS4wLjABAAAA_album"},
        "images": [
            {"url_list": ["https://p3.douyinpic.com/a.jpeg?x=1"], "download_url_list": ["https://p3.douyinpic.com/a_wm.jpeg"]},
            {"url_list": ["https://p3.douyinpic.com/b.webp"], "download_url_list": ["https://p3.douyinpic.com/b_wm.webp"]},
            # 同图集内重复图（查询串不同）应被去重
            {"url_list": ["https://p3.douyinpic.com/a.jpeg?x=2"]},
        ],
        "video": {"cover": {"url_list": []}},
    }
}

DETAIL_LIVE = {
    "aweme_detail": {
        "aweme_id": "7000000000000000002",
        "desc": "Live 图",
        "aweme_type": 68,
        "author": {"nickname": "Live作者"},
        "images": [
            {
                "live_photo_type": 1,
                "video": {"play_addr": {"url_list": ["https://cdn.example/live.mp4?watermark=1&logo_name=x"]}},
                "url_list": ["https://p3.douyinpic.com/live_cover.jpeg"],
            }
        ],
    }
}


# ---------------- 链接提取 ----------------


def test_extract_links_from_share_text():
    text = (
        "7.53 复制打开抖音，看看【作者】的作品 https://v.douyin.com/iRNBho6u/ "
        "还有这个 https://www.douyin.com/video/7534291258635816251 和 "
        "https://www.douyin.com/note/7000000000000000001。"
    )
    links = media.extract_links([text])
    assert links == [
        "https://v.douyin.com/iRNBho6u/",
        "https://www.douyin.com/video/7534291258635816251",
        "https://www.douyin.com/note/7000000000000000001",
    ]


def test_extract_links_ignores_foreign_and_unrelated_urls():
    """非抖音域名、以及抖音的非作品页面都不该被当成作品解析。"""
    links = media.extract_links([
        "https://www.bilibili.com/video/BV1zq836rEbk "
        "https://evil.com/video/123 "
        "https://www.douyin.com/search/abc "
        "https://www.douyin.com/video/7534291258635816251",
    ])
    assert links == ["https://www.douyin.com/video/7534291258635816251"]


def test_extract_links_dedupes_and_handles_bare_host():
    links = media.extract_links([
        "v.douyin.com/iRNBho6u/ 和 v.douyin.com/iRNBho6u/ 再 https://www.douyin.com/user/MS4wLjABAAAA_me",
    ])
    assert links == ["https://v.douyin.com/iRNBho6u/", "https://www.douyin.com/user/MS4wLjABAAAA_me"]


def test_extract_links_tolerates_empty_and_non_string():
    assert media.extract_links([]) == []
    assert media.extract_links(None) == []
    assert media.extract_links([None, 123, ""]) == []


def test_extract_from_json_miniprogram_card():
    """QQ 小程序卡片：从 meta.*.qqdocurl 取链接。"""
    card = {
        "app": "com.tencent.miniapp_01",
        "meta": {
            "detail_1": {
                "title": "抖音",
                "qqdocurl": "https://v.douyin.com/iRNBho6u/?_wv=1",
            }
        },
    }
    assert media.extract_from_json([card]) == ["https://v.douyin.com/iRNBho6u/?_wv=1"]


def test_extract_from_json_ignores_other_apps_and_bad_json():
    other = {"meta": {"detail_1": {"qqdocurl": "https://www.bilibili.com/video/BV1zq836rEbk"}}}
    assert media.extract_from_json([other]) == []
    assert media.extract_from_json(["{不是 json", None, 42]) == []


# ---------------- ID 提取 ----------------


def test_aweme_id_from_url_variants():
    assert media.aweme_id_from_url("https://www.douyin.com/video/7534291258635816251") == "7534291258635816251"
    assert media.aweme_id_from_url("https://www.douyin.com/note/7000000000000000001") == "7000000000000000001"
    assert media.aweme_id_from_url("https://www.douyin.com/aweme/detail/7534291258635816251") == "7534291258635816251"
    assert media.aweme_id_from_url("https://www.iesdouyin.com/share/video/7534291258635816251/") == "7534291258635816251"
    # 短链本身不含 ID（需跟随跳转）
    assert media.aweme_id_from_url("https://v.douyin.com/iRNBho6u/") is None
    assert media.aweme_id_from_url("") is None


def test_aweme_id_from_html():
    html = '<script>window.__DATA__ = {"aweme_id":"7534291258635816251"}</script>'
    assert media.aweme_id_from_html(html) == "7534291258635816251"
    assert media.aweme_id_from_html("<html>无</html>") is None
    # 过短的数字串不算（避免把无关 ID 当作品）
    assert media.aweme_id_from_html('{"aweme_id":"123"}') is None


def test_sec_uid_from_url():
    assert media.sec_uid_from_url("https://www.douyin.com/user/MS4wLjABAAAA_me?from_tab_name=main") == "MS4wLjABAAAA_me"
    assert media.sec_uid_from_url("https://www.douyin.com/video/123") is None


def test_is_allowed_url():
    assert media.is_allowed_url("https://v.douyin.com/x/")
    assert media.is_allowed_url("https://www.iesdouyin.com/share/video/1")
    assert not media.is_allowed_url("https://douyin.com.evil.com/x")
    assert not media.is_allowed_url("not a url")


# ---------------- 内容类型 ----------------


def test_content_type():
    assert media.content_type(DETAIL_VIDEO) == "video"
    assert media.content_type(DETAIL_ALBUM) == "image"
    # aweme_type 缺失但有 images → 仍判图集
    assert media.content_type({"aweme_detail": {"images": [{"url_list": ["u"]}]}}) == "image"
    assert media.content_type({"aweme_detail": {}}) == "video"


# ---------------- 无水印视频地址 ----------------


def test_extract_video_qualities_prefers_bit_rate_and_dedupes():
    qualities = media.extract_video_qualities(DETAIL_VIDEO)
    assert [q["url"] for q in qualities] == ["https://cdn.example/1080.mp4", "https://cdn.example/540.mp4"]
    assert qualities[0]["ratio"] == "1080p"
    assert qualities[0]["quality_label"] == "1080p (2500Kbps)"
    assert qualities[1]["quality_label"] == "540p (800Kbps)"


def test_extract_nwm_url_strips_watermark_marker():
    data = {"aweme_detail": {"video": {"play_addr": {"url_list": ["https://x/playwm/?video_id=abc&ratio=720p"]}}}}
    url = media.extract_nwm_url(data)
    assert url == "https://x/play/?video_id=abc&ratio=720p"
    assert "playwm" not in url


def test_extract_video_qualities_falls_back_to_uri():
    """既无 bit_rate 也无 url_list 时，用 uri 拼装常见档位。"""
    data = {"aweme_detail": {"video": {"play_addr": {"uri": "v0300abc"}}}}
    qualities = media.extract_video_qualities(data)
    assert len(qualities) == 5
    assert qualities[0]["ratio"] == "1080p"
    assert "video_id=v0300abc" in qualities[0]["url"]


def test_extract_video_qualities_empty():
    assert media.extract_video_qualities({}) == []
    assert media.extract_nwm_url({"aweme_detail": {}}) is None


def test_pick_quality_prefers_requested_then_falls_back_to_best():
    qualities = media.extract_video_qualities(DETAIL_VIDEO)
    assert media.pick_quality(qualities, "highest")["ratio"] == "1080p"
    assert media.pick_quality(qualities, "")["ratio"] == "1080p"
    assert media.pick_quality(qualities, "540p")["ratio"] == "540p"
    # 指定档位不存在时回落最高可用，而不是整条降级成文本
    assert media.pick_quality(qualities, "480p")["ratio"] == "1080p"
    assert media.pick_quality([], "highest") is None


# ---------------- 图集 ----------------


def test_extract_image_data_static_album_dedupes():
    data = media.extract_image_data(DETAIL_ALBUM)
    assert data["image_count"] == 2  # 第 3 张与第 1 张同路径（仅查询串不同）
    assert data["image_urls"] == [
        "https://p3.douyinpic.com/a.jpeg?x=1",
        "https://p3.douyinpic.com/b.webp",
    ]
    assert data["is_live"] is False
    assert data["preview_url"] == "https://p3.douyinpic.com/a.jpeg?x=1"


def test_extract_image_data_live_photo_uses_mp4():
    """Live 图必须取 mp4（video.play_addr），不是静态封面。"""
    data = media.extract_image_data(DETAIL_LIVE)
    assert data["is_live"] is True
    assert data["image_urls"] == ["https://cdn.example/live.mp4"]
    assert data["image_count"] == 1


def test_extract_image_data_watermark_fallback():
    """只有 download_url_list 时退而用带水印地址，而不是判为无图。"""
    data = media.extract_image_data({"aweme_detail": {"images": [
        {"download_url_list": ["https://p3.douyinpic.com/wm.jpeg"]},
    ]}})
    assert data["image_urls"] == ["https://p3.douyinpic.com/wm.jpeg"]
    assert data["is_live"] is False


def test_extract_image_data_none_without_images():
    assert media.extract_image_data(DETAIL_VIDEO) is None
    assert media.extract_image_data({"aweme_detail": {"images": []}}) is None


# ---------------- 元信息与缓存命名 ----------------


def test_build_meta_video():
    meta = media.build_meta(DETAIL_VIDEO)
    assert meta["aweme_id"] == "7534291258635816251"
    assert meta["content_type"] == "video"
    assert meta["author_nickname"] == "作者名"
    assert meta["cover_url"] == "https://p3.douyinpic.com/cover.jpeg"
    assert meta["duration"] == 12500
    assert meta["url"] == "https://www.douyin.com/video/7534291258635816251"


def test_build_meta_album_uses_first_image_as_cover():
    meta = media.build_meta(DETAIL_ALBUM)
    assert meta["content_type"] == "image"
    assert meta["image_count"] == 2
    assert meta["cover_url"] == "https://p3.douyinpic.com/a.jpeg?x=1"


def test_guess_ext():
    assert media.guess_ext("https://x/a.jpeg") == ".jpg"
    assert media.guess_ext("https://x/a.webp?y=1") == ".webp"
    assert media.guess_ext("https://x/live.mp4") == ".mp4"
    assert media.guess_ext("https://x/noext") == ".jpg"
    assert media.guess_ext("") == ".jpg"


def test_cache_paths(tmp_path):
    assert media.video_cache_path(str(tmp_path), "7534291258635816251").endswith("7534291258635816251.mp4")
    album = media.album_cache_path(str(tmp_path), "7000000000000000001", 2, "https://x/b.webp")
    assert album.endswith("7000000000000000001_002.webp")
