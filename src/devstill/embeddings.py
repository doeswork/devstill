from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Lock
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
        self._state = "cold"
        self._load_lock = Lock()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    def warm(self, on_loading: Callable[[], None] | None = None) -> Any:
        return self._load(on_loading)

    def _load(self, on_loading: Callable[[], None] | None = None) -> Any:
        if self._model is not None:
            return self._model
        with self._load_lock:
            if self._model is not None:
                return self._model
            self._state = "loading"
            if on_loading is not None:
                on_loading()
            from sentence_transformers import SentenceTransformer

            try:
                with warnings.catch_warnings():
                    warnings.filterwarnings(
                        "ignore",
                        message=(
                            r"optimum is not installed\. To use OnnxConfig and "
                            r"BertOnnxConfig, make sure that `optimum` package is "
                            r"installed"
                        ),
                        category=UserWarning,
                    )
                    model = SentenceTransformer(
                        str(self.model_path),
                        trust_remote_code=True,
                        local_files_only=True,
                        device="cpu",
                    )
                model.max_seq_length = self.max_sequence_length
            except Exception as exc:
                self._model = None
                self._state = "cold"
                raise RuntimeError(
                    f"Could not load the offline embedding model at {self.model_path}. "
                    "Run `devstill doctor --load-model` for details."
                ) from exc
            self._model = model
            self._state = "warm"
        return self._model

    def close(self) -> None:
        with self._load_lock:
            self._model = None
            self._state = "cold"

    def encode(
        self,
        texts: Sequence[str],
        show_progress: bool = False,
        progress: Callable[[int, int], None] | None = None,
    ) -> list[list[float]]:
        if not texts:
            return []
        model = self._load()
        order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
        sorted_texts = [texts[index] for index in order]
        restored: list[list[float] | None] = [None] * len(texts)
        if progress is None:
            values = model.encode(
                sorted_texts,
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
                show_progress_bar=show_progress,
            )
            for sorted_index, original_index in enumerate(order):
                restored[original_index] = values[sorted_index].tolist()
            return [value for value in restored if value is not None]

        reporting_group = max(self.batch_size, min(self.batch_size * 8, 128))
        total = len(sorted_texts)
        for start in range(0, total, reporting_group):
            end = min(start + reporting_group, total)
            values = model.encode(
                sorted_texts[start:end],
                batch_size=self.batch_size,
                normalize_embeddings=self.normalize,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            for offset, original_index in enumerate(order[start:end]):
                restored[original_index] = values[offset].tolist()
            progress(end, total)
        return [value for value in restored if value is not None]
