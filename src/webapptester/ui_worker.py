"""Run one dashboard request without putting scenario text in process arguments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("request", type=Path)
    request_path = parser.parse_args(argv).request.resolve()
    with request_path.open(encoding="utf-8") as handle:
        request = json.load(handle)
    if not isinstance(request, dict):
        raise ValueError("dashboard request is invalid")
    storage_state = request.get("storage_state")
    options = argparse.Namespace(
        scenario=request["scenario"],
        url=request["url"],
        provider=request["provider"],
        model=request["model"],
        output=request_path.parent,
        max_steps=request["max_steps"],
        ablation_variant=request["variant"],
        storage_state=Path(storage_state) if storage_state else None,
    )
    code, _report = cli.run(options)
    return code


if __name__ == "__main__":
    sys.exit(main())
