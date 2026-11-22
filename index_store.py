"""Persistent vector index on disk.

Layout under index/:
  vectors.npz   — chunk vectors (and the TF-IDF vocabulary when used)
  chunks.json   — [{text, source, title}] aligned with the vectors

Cosine similarity is plain numpy (vectors are L2-normalised at embed
time, so similarity is a dot product). No FAISS dependency.
"""
import json
import pickle

import numpy as np

import config
from embed import Embedder


class IndexStore:
    def __init__(self, embedder: Embedder | None = None):
        self.dir = config.INDEX_DIR
        self.embedder = embedder or Embedder()
        self.vectors: np.ndarray | None = None
        self.chunks: list[dict] = []
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self):
        vec_file = self.dir / "vectors.npz"
        chunk_file = self.dir / "chunks.json"
        if vec_file.exists() and chunk_file.exists():
            data = np.load(vec_file, allow_pickle=True)
            self.vectors = data["vectors"]
            self.chunks = json.loads(chunk_file.read_text(encoding="utf-8"))
            if "vectorizer" in data.files and data["vectorizer"] is not None:
                self.embedder.set_vectorizer(pickle.loads(data["vectorizer"]))

    def _save(self):
        self.dir.mkdir(parents=True, exist_ok=True)
        vec = self.embedder.get_vectorizer()
        np.savez(
            self.dir / "vectors.npz",
            vectors=self.vectors,
            vectorizer=pickle.dumps(vec) if vec is not None else None,
        )
        (self.dir / "chunks.json").write_text(
            json.dumps(self.chunks, ensure_ascii=False), encoding="utf-8"
        )

    # -- indexing --------------------------------------------------------
    def add_chunks(self, new_chunks: list[dict]) -> int:
        """Add [{text, source, title}] chunks; returns total chunk count."""
        if not new_chunks:
            return len(self.chunks)
        texts = [c["text"] for c in new_chunks]
        if self.vectors is None or len(self.chunks) == 0:
            self.vectors = self.embedder.fit_transform(texts)
            self.chunks = list(new_chunks)
        else:
            # With TF-IDF the vocabulary is fixed by the first corpus;
            # new chunks are encoded against it (standard behaviour).
            new_vecs = self.embedder.encode(texts)
            self.vectors = np.vstack([self.vectors, new_vecs])
            self.chunks.extend(new_chunks)
        self._save()
        return len(self.chunks)

    def build_ephemeral(self, chunks: list[dict]) -> None:
        """Build an in-memory-only index (used for one-off web answers)."""
        texts = [c["text"] for c in chunks]
        self.vectors = self.embedder.fit_transform(texts)
        self.chunks = list(chunks)

    # -- retrieval -------------------------------------------------------
    def search(self, query: str, top_k: int = config.TOP_K) -> list[dict]:
        if self.vectors is None or not self.chunks:
            return []
        q = self.embedder.encode([query])[0]
        scores = self.vectors @ q
        order = np.argsort(-scores)[:top_k]
        out = []
        for i in order:
            item = dict(self.chunks[int(i)])
            item["score"] = float(scores[int(i)])
            out.append(item)
        return out

    def __len__(self):
        return len(self.chunks)
