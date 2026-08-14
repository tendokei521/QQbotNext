"""通用关键词匹配库。

被 msg_text_reply / request_group / request_private 共享，
支持 and/or/text/at/selfid/userid 递归匹配：
- text    : 文本关键词（`text == "selfid"` → 替换为 self_id；`"userid"` → user_id）
- at      : 群 @ 匹配（all=任意@；target=@除自己；self=@自己；具体 qq=@指定人）
- and     : 全部命中才成立
- or      : 任一命中即成立，`value` 为最少命中数
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def match_keywords(
    keywords_data: Dict[str, Any],
    msgtext: Iterable[str],
    *,
    atlist: Optional[Iterable] = None,
    self_id: Any = "",
    user_id: Any = "",
) -> List[str]:
    """匹配关键词配置，返回命中的关键词列表（空 = 未命中）。

    Args:
        keywords_data: 关键词配置（含 text/at/and/or/value 键）
        msgtext: 文本片段列表
        atlist: @ 目标的 qq 列表（可选）
        self_id / user_id: 用于替换 text=="selfid"/"userid"
    """
    msgtext = [str(t) for t in (msgtext or [])]
    atlist = [str(a) for a in (atlist or [])]
    if not msgtext and not atlist:
        return []

    def _get_keyword(keyword: Dict[str, Any], texts: List[str]) -> List[str]:
        got: List[str] = []
        textkeywords = keyword.get("text")
        if textkeywords == "selfid":
            textkeywords = str(self_id)
        elif textkeywords == "userid":
            textkeywords = str(user_id)
        if textkeywords and texts:
            for text in texts:
                if textkeywords in text:
                    got.append(textkeywords)

        at = keyword.get("at")
        if at and atlist:
            for attarget in atlist:
                if at == "all":
                    got.append(f"@{attarget}")
                elif at == "target":
                    if attarget != str(self_id):
                        got.append(f"@{attarget}")
                elif at == "self":
                    if attarget == str(self_id):
                        got.append(f"@{attarget}")
                else:
                    if at == str(attarget):
                        got.append(f"@{attarget}")

        anddata = keyword.get("and") or []
        if anddata:
            for andkeyword in anddata:
                sub = _get_keyword(andkeyword, texts)
                if not sub:
                    got.clear()
                    break
                got.extend(sub)

        ordata = keyword.get("or") or []
        if ordata:
            for orkeyword in ordata:
                sub = _get_keyword(orkeyword, texts)
                if sub:
                    got.extend(sub)
            min_text = keyword.get("value", 0)
            if min_text and min_text > len(got):
                got.clear()
        return got

    return _get_keyword(keywords_data, msgtext)
