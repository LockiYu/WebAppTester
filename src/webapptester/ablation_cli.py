"""Run an external B0--B3 manifest with Ollama or budgeted DeepSeek Flash."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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
        description="Run an external scenario manifest under B0-B3 conditions"
    )
    parser.add_argument("manifest", type=Path, help="JSON scenario manifest supplied by the evaluator")
    parser.add_argument("--output", type=Path, required=True, help="Output directory for ablation reports")
    parser.add_argument("--provider", choices=("ollama", "deepseek"), default="ollama")
    parser.add_argument("--model", help="Model; defaults to qwen2.5:3b or deepseek-flash")
    parser.add_argument("--variant", choices=VARIANT_ORDER, nargs="+", default=VARIANT_ORDER)
    return parser


def run_manifest(
    manifest: Path,
    output: Path,
    *,
    model: str | None = None,
    provider: str = "ollama",
    variants: tuple[str, ...] = VARIANT_ORDER,
) -> Path:
    """Run each supplied case once per condition with one selected provider."""

    if provider not in {"ollama", "deepseek"}:
        raise ValueError("unsupported ablation provider")
    if provider == "deepseek":
        if model not in {None, "deepseek-flash"}:
            raise ValueError("formal DeepSeek runs require deepseek-flash")
        if not os.environ.get("DEEPSEEK_API_KEY"):
            raise ValueError("DeepSeek API key is unavailable")
    selected_model = model or ("deepseek-flash" if provider == "deepseek" else "qwen2.5:3b")

    cases = load_scenario_manifest(manifest)
    output = Path(output)

    def runner(case, config):
        report_dir = output / config.variant / case.scenario_id
        args = argparse.Namespace(
            scenario=case.scenario.text,
            url=case.scenario.url,
            provider=provider,
            model=selected_model,
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
    return write_summary(
        output / "ablation-summary.json", outcomes,
        run_config={
            "provider": provider,
            "model": selected_model,
            "manifest_sha256": hashlib.sha256(Path(manifest).read_bytes()).hexdigest(),
        },
    )


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        path = run_manifest(
            args.manifest, args.output, model=args.model,
            provider=args.provider, variants=tuple(args.variant),
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"UNKNOWN: ablation could not run ({type(error).__name__})", file=sys.stderr)
        return 2
    print(f"ablation summary={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
