"""Local-only command for running an external B0--B3 scenario manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import cli
from .ablation import (
    AblationHarness,
    VARIANT_ORDER,
    load_scenario_manifest,
    write_summary,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run an external scenario manifest under local B0-B3 conditions"
    )
    parser.add_argument("manifest", type=Path, help="JSON scenario manifest supplied by the evaluator")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for ablation reports")
    parser.add_argument("--model", default="qwen2.5:3b", help="Local Ollama model")
    parser.add_argument("--variant", choices=VARIANT_ORDER, nargs="+", default=VARIANT_ORDER)
    return parser


def run_manifest(
    manifest: Path,
    output: Path,
    *,
    model: str = "qwen2.5:3b",
    variants: tuple[str, ...] = VARIANT_ORDER,
) -> Path:
    """Run each supplied case once for each selected local condition."""

    cases = load_scenario_manifest(manifest)
    output = Path(output)

    def runner(case, config):
        report_dir = output / config.variant / case.scenario_id
        args = argparse.Namespace(
            scenario=case.scenario.text,
            url=case.scenario.url,
            provider="ollama",
            model=model,
            output=report_dir,
            max_steps=case.scenario.max_steps,
            storage_state=case.storage_state,
            ablation_variant=config.variant,
        )
        _exit_code, report_path = cli.run(args)
        with Path(report_path).open("r", encoding="utf-8") as handle:
            report = json.load(handle)
        verdict = report.get("verdict", {})
        return {
            "status": verdict.get("status"),
            "reason": verdict.get("reason", ""),
            "evidence": verdict.get("evidence", []),
            "checkpoints": report.get("checkpoints", []),
            "steps": report.get("steps", 0),
            "token_usage": report.get("token_usage", {}),
            "elapsed_seconds": report.get("elapsed_seconds", 0.0),
        }

    outcomes = AblationHarness(runner).run(cases, variants)
    return write_summary(output / "ablation-summary.json", outcomes)


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path = run_manifest(args.manifest, args.output, model=args.model, variants=tuple(args.variant))
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"UNKNOWN: ablation could not run ({type(error).__name__})", file=sys.stderr)
        return 2
    print(f"ablation summary={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
