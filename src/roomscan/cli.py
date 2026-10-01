"""Command line: `roomscan run <capture>` — one command per capture, tier auto-detected."""
from __future__ import annotations

import json
import time
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Phone capture -> measured, stitched floor plan.")


@app.command()
def run(capture: Path = typer.Argument(..., help="Stray Scanner folder, video file, or folder of per-room photo folders"),
        out: Path = typer.Option(None, "--out", "-o", help="Output folder (default runs/<capture name>)"),
        tier: str = typer.Option("auto", help="auto | lidar | video | photo"),
        stride: int = typer.Option(5, help="LiDAR: use every Nth frame"),
        no_drift: bool = typer.Option(False, "--no-drift", help="Disable drift correction (ablation)"),
        no_damage: bool = typer.Option(False, "--no-damage", help="Skip damage detection"),
        no_cache: bool = typer.Option(False, "--no-cache", help="Recompute everything"),
        quiet: bool = typer.Option(False, "--quiet", "-q")):
    """Process one capture into result.json + plan.png."""
    from roomscan.pipeline import run as run_pipeline

    out = out or Path("runs") / capture.name
    t = time.time()
    res = run_pipeline(capture, out, tier=tier, stride=stride, drift=not no_drift, damage=not no_damage,
                       use_cache=not no_cache, progress=not quiet)
    fp = res["property"]["footprint_area"]
    typer.echo(f"[{res['capture']['tier']}] {len(res['rooms'])} rooms, footprint {fp['value']:.2f} m2 "
               f"(90% CI {fp['ci90'][0]:.2f}-{fp['ci90'][1]:.2f}) in {time.time() - t:.1f}s -> {out}")


@app.command()
def schema(out: Path = typer.Option(Path("schema/output.schema.json"))):
    """Write the JSON schema of the output contract."""
    from roomscan.schema import Output

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(Output.model_json_schema(), indent=2))
    typer.echo(f"wrote {out}")


if __name__ == "__main__":
    app()
