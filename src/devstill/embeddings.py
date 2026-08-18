from __future__ import annotations

import warnings
from collections.abc import Callable, Sequence
from pathlib import Path
from threading import Lock, RLock
from typing import Any

from tqdm import tqdm

from devstill.devices import (
    EmbeddingDevice,
    cuda_out_of_memory_message,
    inspect_device,
    is_cuda_out_of_memory,
)

_TQDM_THREAD_LOCK = RLock()


def _configure_tqdm_thread_lock() -> None:
    """Keep hidden Sentence Transformers progress local to this process."""
    tqdm.set_lock(_TQDM_THREAD_LOCK)


class EmbeddingBackend:
    def __init__(
        self,
        model_path: Path,
        max_sequence_length: int = 2048,
        batch_size: int = 8,
        normalize: bool = True,
        device: EmbeddingDevice = "auto",
    ) -> None:
        _configure_tqdm_thread_lock()
        self.model_path = model_path
        self.max_sequence_length = max_sequence_length
        self.batch_size = batch_size
        self.normalize = normalize
        self.requested_device = device
        self._resolved_device: str | None = None
        self._model: Any = None
        self._state = "cold"
        self._load_lock = Lock()

    @property
    def state(self) -> str:
        return self._state

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def device(self) -> str:
        if self._resolved_device is None:
            self._resolved_device = inspect_device(self.requested_device).resolved
        return self._resolved_device

    def warm(self, on_loading: Callable[[], None] | None = None) -> Any:
        return self._load(on_loading)

    def _load(self, on_loading: Callable[[], None] | None = None) -> Any:
        _configure_tqdm_thread_lock()
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
                        device=self.device,
                    )
                model.max_seq_length = self.max_sequence_length
            except Exception as exc:
                self._model = None
                self._state = "cold"
                if is_cuda_out_of_memory(exc):
                    raise RuntimeError(
                        cuda_out_of_memory_message(self.batch_size)
                    ) from exc
                raise RuntimeError(
                    f"Could not load the offline embedding model at {self.model_path}. "
                    f"Requested device: {self.requested_device}; resolved device: "
                    f"{self.device}. Run `devstill doctor --load-model` for details."
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
        _configure_tqdm_thread_lock()
        if not texts:
            return []
        model = self._load()
        order = sorted(range(len(texts)), key=lambda index: len(texts[index]))
        sorted_texts = [texts[index] for index in order]
        restored: list[list[float] | None] = [None] * len(texts)
        if progress is None:
            try:
                values = model.encode(
                    sorted_texts,
                    batch_size=self.batch_size,
                    normalize_embeddings=self.normalize,
                    convert_to_numpy=True,
                    show_progress_bar=show_progress,
                )
            except Exception as exc:
                if is_cuda_out_of_memory(exc):
                    raise RuntimeError(
                        cuda_out_of_memory_message(self.batch_size)
                    ) from exc
                raise
            for sorted_index, original_index in enumerate(order):
                restored[original_index] = values[sorted_index].tolist()
            return [value for value in restored if value is not None]

        reporting_group = max(self.batch_size, min(self.batch_size * 8, 128))
        total = len(sorted_texts)
        for start in range(0, total, reporting_group):
            end = min(start + reporting_group, total)
            try:
                values = model.encode(
                    sorted_texts[start:end],
                    batch_size=self.batch_size,
                    normalize_embeddings=self.normalize,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
            except Exception as exc:
                if is_cuda_out_of_memory(exc):
                    raise RuntimeError(
                        cuda_out_of_memory_message(self.batch_size)
                    ) from exc
                raise
            for offset, original_index in enumerate(order[start:end]):
                restored[original_index] = values[offset].tolist()
            progress(end, total)
        return [value for value in restored if value is not None]
