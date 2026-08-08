from __future__ import annotations

import argparse
from pathlib import Path

RPC_IMPORT = "import torch.distributed.rpc\n"
RPC_REPLACEMENT = """\
import torch.distributed
torch.distributed.rpc = types.ModuleType("torch.distributed.rpc")
torch.distributed.rpc.is_available = lambda: False
sys.modules["torch.distributed.rpc"] = torch.distributed.rpc
"""
FUNCTIONAL_IMPORT = "import torch.nn.functional as F\n"
FUNCTIONAL_REPLACEMENT = """\
import importlib
F = importlib.import_module("torch.nn.functional")
"""


def patch_torch_jit_internal(target: Path) -> None:
    source = target.read_text(encoding="utf-8")
    if source.count(RPC_IMPORT) != 1:
        raise RuntimeError(
            "unexpected PyTorch _jit_internal.py; refusing an unsafe frozen patch"
        )
    target.write_text(
        source.replace(RPC_IMPORT, RPC_REPLACEMENT),
        encoding="utf-8",
    )
    functional_target = target.parent / "functional.py"
    functional_source = functional_target.read_text(encoding="utf-8")
    if functional_source.count(FUNCTIONAL_IMPORT) != 1:
        raise RuntimeError(
            "unexpected PyTorch functional.py; refusing an unsafe frozen patch"
        )
    functional_target.write_text(
        functional_source.replace(FUNCTIONAL_IMPORT, FUNCTIONAL_REPLACEMENT),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", required=True, type=Path)
    args = parser.parse_args()
    patch_torch_jit_internal(args.target)


if __name__ == "__main__":
    main()
