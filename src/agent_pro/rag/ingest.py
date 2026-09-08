"""文档摄取与入库 (v0.5)

读取文档 → 切分 (langchain-text-splitters) → 向量化 (config.EMBEDDING_MODEL)
→ 写入向量库 (config.VECTOR_STORE_PATH)，供 retrieve.py 检索。
"""

# TODO(v0.5): 实现摄取流水线
#   def ingest(paths, chunk_size, ...) -> None:
#     - 加载文档 (langchain_community.document_loaders)
#     - 切分 (RecursiveCharacterTextSplitter)
#     - 嵌入 + 写入 vectorstore (FAISS/Chroma)
