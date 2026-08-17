from __future__ import annotations

from types import SimpleNamespace

import pytest

from devstill.devices import (
    cuda_out_of_memory_message,
    inspect_device,
    normalize_device,
)


class FakeCuda:
    def __init__(self, available: bool, *, allocation_error: str | None = None) -> None:
        self.available = available
        self.allocation_error = allocation_error

    def is_available(self) -> bool:
        return self.available

    def get_device_name(self, _index: int) -> str:
        return "Test NVIDIA GPU"


def fake_torch(
    available: bool,
    *,
    cuda_build: str | None = None,
    allocation_error: str | None = None,
) -> SimpleNamespace:
    cuda = FakeCuda(available, allocation_error=allocation_error)

    def empty(_size: int, *, device: str):
        assert device == "cuda"
        if allocation_error:
            raise RuntimeError(allocation_error)
        return object()

    return SimpleNamespace(
        __version__="2.7.0",
        version=SimpleNamespace(cuda=cuda_build),
        cuda=cuda,
        empty=empty,
    )


def test_auto_resolves_cpu_or_cuda_from_pytorch_capability() -> None:
    cpu = inspect_device("auto", torch_module=fake_torch(False))
    assert cpu.resolved == "cpu"
    assert not cpu.cuda_available

    cuda = inspect_device(
        "auto",
        torch_module=fake_torch(True, cuda_build="12.8"),
        verify_allocation=True,
    )
    assert cuda.resolved == "cuda"
    assert cuda.cuda_build == "12.8"
    assert cuda.gpu_name == "Test NVIDIA GPU"


def test_explicit_cuda_rejects_unavailable_or_failed_allocation() -> None:
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        inspect_device("cuda", torch_module=fake_torch(False))
    with pytest.raises(RuntimeError, match="CUDA was requested"):
        inspect_device(
            "cuda",
            torch_module=fake_torch(True, allocation_error="driver mismatch"),
            verify_allocation=True,
        )


def test_cpu_remains_cpu_even_when_cuda_is_available() -> None:
    info = inspect_device("cpu", torch_module=fake_torch(True, cuda_build="12.8"))
    assert info.resolved == "cpu"
    assert info.cuda_available


def test_device_normalization_and_oom_guidance() -> None:
    assert normalize_device(" CUDA ") == "cuda"
    with pytest.raises(ValueError, match="auto, cpu, cuda"):
        normalize_device("mps")
    message = cuda_out_of_memory_message(16)
    assert "below 16" in message
    assert "devstill device cpu" in message
