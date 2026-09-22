"""ChromaDB trong bộ nhớ + embedding giả cho test — không đụng ChromaDB/collection thật của bot."""
import itertools
import unittest
from unittest import mock

import chromadb
import numpy as np

from core import rag_engine
from tests.helpers import fake_embed

_bot_ids = itertools.count(900_000)  # id bot thử, không trùng bot thật


class InMemoryChroma(unittest.TestCase):
    """Mỗi test có bot_id riêng; collection của test được xóa khi xong."""

    def setUp(self):
        self.bot_id = next(_bot_ids)
        client = chromadb.EphemeralClient()
        for patcher in (
            mock.patch.object(rag_engine, "chroma_client", client),
            mock.patch.object(rag_engine, "embed_texts", fake_embed),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.addCleanup(self._drop_collections, client)

    def _drop_collections(self, client):
        for collection in client.list_collections():
            name = getattr(collection, "name", collection)
            if name.endswith(str(self.bot_id)):
                client.delete_collection(name)

    def add_document(self, document_id: int, texts: list[str]) -> None:
        rag_engine.upsert_chunks(self.bot_id, document_id, [{"content": t, "metadata": {}} for t in texts])

    @staticmethod
    def cosine(a: str, b: str) -> float:
        return float(np.dot(fake_embed([a])[0], fake_embed([b])[0]))
