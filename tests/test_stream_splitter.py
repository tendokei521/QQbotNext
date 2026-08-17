"""流式句子切分器测试。"""

from app.llm.splitter import split_sentences, strip_stream_artifacts


def test_split_sentences_basic():
    sentences, remainder = split_sentences("你好。世界！这是测试", max_length=50)
    assert sentences == ["你好。", "世界！"]
    assert remainder == "这是测试"


def test_split_sentences_combined_punctuation():
    sentences, remainder = split_sentences("真的吗？！不是吧！？", max_length=50)
    assert sentences == ["真的吗？！", "不是吧！？"]
    assert remainder == ""


def test_split_sentences_combined_ascii_punctuation():
    sentences, remainder = split_sentences("What?! Really!?", max_length=50)
    assert sentences == ["What?!", "Really!?"]
    assert remainder == ""


def test_split_sentences_long_without_delimiter():
    text = "a" * 120
    sentences, remainder = split_sentences(text, max_length=50)
    assert len(sentences) == 2
    assert all(len(s) == 50 for s in sentences)
    assert remainder == "a" * 20


def test_split_sentences_newline():
    sentences, remainder = split_sentences("第一行\n第二行", max_length=50)
    assert sentences == ["第一行"]
    assert remainder == "第二行"


def test_strip_stream_artifacts():
    assert strip_stream_artifacts("rate.") == ""
    assert strip_stream_artifacts(" rate. ") == ""
    assert strip_stream_artifacts("Rate.") == ""
    assert strip_stream_artifacts("rate.晚安，不送了。") == "晚安，不送了。"
    assert strip_stream_artifacts("晚安，不送了。") == "晚安，不送了。"
    assert strip_stream_artifacts("睡觉去。rate.") == "睡觉去。"
    assert strip_stream_artifacts("睡觉去。rate. 明天见") == "睡觉去。 明天见"
    # 不能误伤正常英文句子
    assert strip_stream_artifacts("I rate. This is good.") == "I rate. This is good."
    assert strip_stream_artifacts("Hello rate.") == "Hello rate."
