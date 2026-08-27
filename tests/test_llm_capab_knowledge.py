"""Provider 能力分发 / 知识库存储基础测试。"""

import pytest

from app.llm.knowledge.store import KnowledgeStore, cosine
from app.llm.providers.embedding import OpenAIEmbeddingProvider
from app.llm.providers.rerank import CohereRerankProvider, JinaRerankProvider
from app.llm.providers.stt import OpenAIWhisperSTTProvider
from app.llm.providers.tts import OpenAITTSProvider
from app.llm.providers import get_provider


def test_get_provider_dispatches_by_provider_type():
    assert isinstance(
        get_provider({"provider_type": "embedding", "provider": "openai", "api_key": "x"}),
        OpenAIEmbeddingProvider,
    )
    assert isinstance(
        get_provider({"provider_type": "rerank", "provider": "jina", "api_key": "x"}),
        JinaRerankProvider,
    )
    assert isinstance(
        get_provider({"provider_type": "rerank", "provider": "cohere", "api_key": "x"}),
        CohereRerankProvider,
    )
    assert isinstance(
        get_provider({"provider_type": "tts", "provider": "openai", "api_key": "x"}),
        OpenAITTSProvider,
    )
    assert isinstance(
        get_provider({"provider_type": "stt", "provider": "openai", "api_key": "x"}),
        OpenAIWhisperSTTProvider,
    )


def test_knowledge_store_cosine_and_crud(tmp_path, monkeypatch):
    # KnowledgeStore 使用 app.llm.llm_data_dir；测试隔离到临时目录
    monkeypatch.setenv("QQBOT_LLM_DATA_DIR", str(tmp_path / "llm"))
    store = KnowledgeStore("bot1")
    try:
        cid = store.add("今天天气很好", title="测试", embedding=[1.0, 0.0])
        assert cid
        hits = store.search([0.9, 0.1], limit=5)
        assert hits and len(hits[0]["content"]) > 0
        assert hits[0]["_score"] > 0.9
        assert store.delete(cid) is True
        assert store.get(cid) is None
    finally:
        store.close()


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) > 0.999
    assert cosine([1, 0], [0, 1]) < 0.001
