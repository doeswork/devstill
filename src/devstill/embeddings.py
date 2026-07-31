from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any


class EmbeddingBackend:
    def __init__(
        self,
        model_path: Path,
        max_sequence_length: int = 2048,
        batch_size: int = 8,
        normalize: bool = True,
    ) -> None:
        self.model_path = model_path
        self.max_sequence_length = max_sequence_length
        self.batch_size = batch_size
        self.normalize = normalize
        self._model: Any = None

    def _load(self) -> Any:
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            try:
                self._model = SentenceTransformer(
                    str(self.model_path),
                    trust_remote_code=True,
                    local_files_only=True,
                    device="cpu",
                )
            except Exception as exc:
                raise RuntimeError(
                    f"Could not load the offline embedding model at {self.model_path}. "
                    "Run `devstill doctor --load-model` for details."
                ) from exc
            self._model.max_seq_length = self.max_sequence_length
        return self._model

    def encode(
        self, texts: Sequence[str], show_progress: bool = False
    ) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
        sorted_texts = [texts[index] for index in order]
        values = model.encode(
            sorted_texts,
            batch_size=self.batch_size,
            normalize_embeddings=self.normalize,
            convert_to_numpy=True,
            show_progress_bar=show_progress,
        )
        restored: list[list[float] | None] = [None] * len(texts)
        for sorted_index, original_index in enumerate(order):
            restored[original_index] = values[sorted_index].tolist()
        return [value for value in restored if value is not None]
