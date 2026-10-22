"""知识库模块的配置 Schema（原 agent「知识库」组，共 3 键）。"""

SCHEMA = {
    "knowledge_enable": {
        "type": "boolean",
        "label": "启用知识库",
        "description": "开启后可向知识库写入/检索资料",
        "default": False,
    },
    "knowledge_embedding_model_id": {
        "type": "resource",
        "resource": "provider_model",
        "label": "Embedding 模型",
        "description": "知识库向量化用的模型实例",
        "default": "",
        "filters": {"provider_type": "embedding"},
    },
    "knowledge_recall_limit": {
        "type": "number",
        "label": "检索条数上限",
        "description": "一次检索最多返回几段",
        "default": 5,
    },
}
