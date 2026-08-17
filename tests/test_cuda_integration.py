from __future__ import annotations

import pytest


@pytest.mark.cuda
def test_pytorch_can_allocate_on_cuda() -> None:
    torch = pytest.importorskip("torch")
    if not torch.cuda.is_available():
        pytest.skip("CUDA is not available on this runner")
    value = torch.empty(1, device="cuda")
    assert value.device.type == "cuda"
    assert torch.cuda.get_device_name(0)
