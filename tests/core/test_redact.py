"""日志脱敏（``app/core/redact.py``）与 Formatter 挂钩测试。

锁住四件事：

1. **压得掉**：``data:...;base64,<大载荷>`` 与 ``base64://<大载荷>`` 变成 ``[base64 4.9KB]``；
2. **不乱压**：短载荷、非 base64 文本原样返回（快筛命中即返回同一对象）；
3. **体积标签**：B / KB / MB 三档换算；
4. **端到端**：``CallerModuleFormatter`` 真的把载荷挡在 handler 之前——含 ``args`` 形态
   （``logger.info("%s", payload)``）也算，因为那是靠 ``getMessage()`` 合并后再替换的。
"""

import logging

from app.core import redact
from app.core.logger import CallerModuleFormatter

PNG_PREFIX = "data:image/png;base64,"
BIG = "A" * 5000


def _record(msg, args=None) -> logging.LogRecord:
    return logging.LogRecord(
        name="[Service]",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )


# ==================== 体积标签 ====================


def test_size_label_switches_units():
    assert redact.size_label(512) == "512B"
    assert redact.size_label(5000) == "4.9KB"
    assert redact.size_label(3 * 1024 * 1024) == "3.0MB"


def test_placeholder_shape():
    assert redact.placeholder(5000) == "[base64 4.9KB]"


# ==================== 文本级脱敏 ====================


def test_redact_data_url_payload():
    text = f'{{"url": "{PNG_PREFIX}{BIG}"}}'

    out = redact.redact_payloads(text)

    assert BIG[:100] not in out
    assert f"{PNG_PREFIX}[base64 4.9KB]" in out


def test_redact_base64_scheme_payload():
    text = f"file=base64://{BIG}"

    out = redact.redact_payloads(text)

    assert BIG[:100] not in out
    assert "base64://[base64 4.9KB]" in out


def test_redact_keeps_short_payloads():
    """低于阈值的小载荷留着：压掉反而妨碍排查小图/图标问题。"""
    text = f"{PNG_PREFIX}QUJD"

    assert redact.redact_payloads(text) == text


def test_redact_leaves_ordinary_text_untouched():
    text = '{"role": "user", "content": "帮我看看这张图"}'

    assert redact.redact_payloads(text) is text  # 快筛命中，原对象返回


def test_redact_ignores_long_but_unrelated_tokens():
    """长 token / 签名长得像 base64，但不在 data URL 里 —— 不能误伤。"""
    token = "A" * 5000
    text = f"Authorization: Bearer {token}"

    assert redact.redact_payloads(text) == text


def test_redact_handles_multiple_payloads():
    text = f"{PNG_PREFIX}{BIG} 与 data:image/jpeg;base64,{'B' * 3000}"

    out = redact.redact_payloads(text)

    assert out.count("[base64 ") == 2
    assert "B" * 100 not in out


def test_shorten_data_url_only_touches_data_urls():
    assert redact.shorten_data_url("https://example.com/a.png") == "https://example.com/a.png"
    assert redact.shorten_data_url(None) is None
    assert redact.shorten_data_url(f"{PNG_PREFIX}{BIG}") == f"{PNG_PREFIX}[base64 4.9KB]"


# ==================== Formatter 端到端 ====================


def test_formatter_redacts_payload_in_msg():
    formatter = CallerModuleFormatter(fmt="%(message)s")

    out = formatter.format(_record(f"payload={PNG_PREFIX}{BIG}"))

    assert BIG[:100] not in out
    assert "[base64 4.9KB]" in out


def test_formatter_redacts_payload_passed_as_args():
    """``logger.info("%s", payload)``：先 getMessage 合并再替换，args 形态同样覆盖。"""
    formatter = CallerModuleFormatter(fmt="%(message)s")

    out = formatter.format(_record("payload=%s", args=(f"{PNG_PREFIX}{BIG}",)))

    assert BIG[:100] not in out
    assert "[base64 4.9KB]" in out


def test_formatter_leaves_clean_records_alone():
    formatter = CallerModuleFormatter(fmt="%(message)s")

    out = formatter.format(_record("普通日志，没有图片"))

    assert out == "普通日志，没有图片"
