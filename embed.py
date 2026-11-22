"""Embeddings.

Primary: HuggingFace Inference API (sentence-transformers/all-MiniLM-L6-v2)
when HF_TOKEN is set. Fallback: sklearn TF-IDF vectors, so retrieval works
with zero API keys. Both return L2-normalised float32 numpy arrays.
"""
import numpy as np
import requests
from sklearn.feature_extraction.text import TfidfVectorizer

import config

HF_API = "https://api-inference.huggingface.co/pipeline/feature-extraction/"


class Embedder:
    """Stateful embedder. TF-IDF needs a fitted vocabulary, so for the
    TF-IDF backend `fit` must be called on the corpus before encoding
    queries."""

    def __init__(self):
        self.backend = config.embed_backend()
        self._vectorizer: TfidfVectorizer | None = None

    # -- corpus side -----------------------------------------------------
    def fit_transform(self, texts: list[str]) -> np.ndarray:
        if self.backend == "huggingface":
            return self._hf_encode(texts)
        self._vectorizer = TfidfVectorizer(
            max_features=4096, stop_words="english", sublinear_tf=True
        )
        mat = self._vectorizer.fit_transform(texts)
        return _normalise(mat.toarray().astype(np.float32))

    # -- query side ------------------------------------------------------
    def encode(self, texts: list[str]) -> np.ndarray:
        if self.backend == "huggingface":
            return self._hf_encode(texts)
        if self._vectorizer is None:
            raise RuntimeError("TF-IDF embedder used before fit_transform()")
        mat = self._vectorizer.transform(texts)
        return _normalise(mat.toarray().astype(np.float32))

    def get_vectorizer(self):
        return self._vectorizer

    def set_vectorizer(self, vectorizer) -> None:
        self._vectorizer = vectorizer

    # -- HuggingFace -----------------------------------------------------
    def _hf_encode(self, texts: list[str]) -> np.ndarray:
        url = HF_API + config.HF_EMBED_MODEL
        headers = {"Authorization": f"Bearer {config.HF_TOKEN}"}
        vectors = []
        # The feature-extraction endpoint accepts a list of inputs.
        for i in range(0, len(texts), 32):
            batch = texts[i : i + 32]
            resp = requests.post(
                url,
                headers=headers,
                json={"inputs": batch, "options": {"wait_for_model": True}},
                timeout=120,
            )
            resp.raise_for_status()
            data = resp.json()
            arr = np.array(data, dtype=np.float32)
            if arr.ndim == 3:  # token-level -> mean pool
                arr = arr.mean(axis=1)
            vectors.append(arr)
        return _normalise(np.vstack(vectors))


def _normalise(mat: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return mat / norms
