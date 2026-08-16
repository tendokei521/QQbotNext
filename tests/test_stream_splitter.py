"""流式句子切分器测试。"""

from app.llm.splitter import split_sentences


def test_split_sentences_basic():
    sentences, remainder = split_sentences("你好。世界！这是测试", max_length=50)
    assert sentences == ["你好。", "世界！"]
    assert remainder == "这是测试"


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
