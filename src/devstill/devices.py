from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, cast

EmbeddingDevice = Literal["auto", "cpu", "cuda"]
EMBEDDING_DEVICES: tuple[EmbeddingDevice, ...] = ("auto", "cpu", "cuda")


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    requested: EmbeddingDevice
    resolved: Literal["cpu", "cuda"]
    torch_version: str
    cuda_build: str | None
    cuda_available: bool
    gpu_name: str | None
    cuda_error: str | None = None


def _torch_module(torch_module: Any | None = None) -> Any:
    if torch_module is not None:
        return torch_module
    try:
        import torch
    except Exception as exc:
        raise RuntimeError(
            "PyTorch is unavailable. Re-run `./setup.sh` to repair the "
            "Devstill installation."
        ) from exc
    return torch


def inspect_device(
    requested: EmbeddingDevice,
    *,
    torch_module: Any | None = None,
    verify_allocation: bool = False,
) -> DeviceInfo:
    torch = _torch_module(torch_module)
    torch_version = str(getattr(torch, "__version__", "unknown"))
    version = getattr(torch, "version", None)
    cuda_build_value = getattr(version, "cuda", None)
    cuda_build = str(cuda_build_value) if cuda_build_value else None
    cuda_available = False
    gpu_name: str | None = None
    cuda_error: str | None = None

    try:
        cuda_available = bool(torch.cuda.is_available())
        if cuda_available:
            gpu_name = str(torch.cuda.get_device_name(0))
            if verify_allocation:
                probe = torch.empty(1, device="cuda")
                del probe
    except Exception as exc:  # noqa: BLE001
        cuda_available = False
        gpu_name = None
        cuda_error = str(exc)

    if requested == "cuda" and not cuda_available:
        detail = f" ({cuda_error})" if cuda_error else ""
        raise RuntimeError(
            "CUDA was requested, but this PyTorch installation cannot use an "
            f"NVIDIA GPU{detail}. Re-run `./setup.sh --device cuda`, check the "
            "NVIDIA driver, or choose `devstill device cpu`."
        )

    use_cuda = requested == "cuda" or requested == "auto" and cuda_available
    resolved: Literal["cpu", "cuda"] = "cuda" if use_cuda else "cpu"
    return DeviceInfo(
        requested=requested,
        resolved=resolved,
        torch_version=torch_version,
        cuda_build=cuda_build,
        cuda_available=cuda_available,
        gpu_name=gpu_name,
        cuda_error=cuda_error,
    )


def normalize_device(value: str) -> EmbeddingDevice:
    normalized = value.strip().casefold()
    if normalized not in EMBEDDING_DEVICES:
        choices = ", ".join(EMBEDDING_DEVICES)
        raise ValueError(f"embedding device must be one of: {choices}")
    return cast(EmbeddingDevice, normalized)


def is_cuda_out_of_memory(exc: BaseException) -> bool:
    message = str(exc).casefold()
    return "cuda" in message and "out of memory" in message


def cuda_out_of_memory_message(batch_size: int) -> str:
    return (
        "CUDA ran out of memory while generating embeddings. Reduce "
        f"embedding.batch_size below {batch_size} in devstill.json, or run "
        "`devstill device cpu`."
    )
