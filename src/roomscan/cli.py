"""Command line: `roomscan run <capture>` — one command per capture, tier auto-detected.

On failure the command prints one line starting with "error:" and exits non-zero: 2 when
the input cannot be used (the message says why and what to do), 1 for anything else (the
traceback is saved to error.log in the output folder; --debug shows it instead).
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import traceback
from pathlib import Path

import typer

app = typer.Typer(add_completion=False, help="Phone capture -> measured, stitched floor plan.",
                  pretty_exceptions_enable=False)


def _safe_console() -> None:
    """Printing a name the console code page cannot show (e.g. output redirected on a
    cp1252 Windows console) must not crash the run: such characters are escaped."""
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="backslashreplace")
        except (AttributeError, ValueError):
            pass


@app.callback()
def _main() -> None:
    _safe_console()


def _one_line(x) -> str:
    return " ".join(str(x).split())


def _save_traceback(out: Path) -> str:
    text = traceback.format_exc()
    for f in (out / "error.log", Path(tempfile.gettempdir()) / "roomscan_error.log"):
        try:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(text, encoding="utf-8")
            return str(f)
        except OSError:
            continue
    return "(not saved: no writable folder)"


def _summary(res: dict, out: Path, seconds: float) -> str:
    try:
        fp = res["property"]["footprint_area"]
        area = f"footprint {fp['value']:.2f} m2 (90% CI {fp['ci90'][0]:.2f}-{fp['ci90'][1]:.2f})"
    except (KeyError, TypeError, ValueError, IndexError):
        area = "footprint not measured"
    return f"[{res.get('capture', {}).get('tier', '?')}] {len(res.get('rooms') or [])} rooms, {area} in {seconds:.1f}s -> {out}"


@app.command()
def run(capture: Path = typer.Argument(..., help="Stray Scanner folder, video file, folder of per-room photo folders, "
                                                 "or a .zip of one of these"),
        out: Path = typer.Option(None, "--out", "-o", help="Output folder (default runs/<capture name>)"),
        tier: str = typer.Option("auto", help="auto | lidar | video | photo"),
        stride: int = typer.Option(5, help="LiDAR: use every Nth frame"),
        drift: str = typer.Option("loop", help="Drift correction: off | loop | heading | loop+heading"),
        no_damage: bool = typer.Option(False, "--no-damage", help="Skip damage detection"),
        no_cache: bool = typer.Option(False, "--no-cache", help="Recompute everything"),
        quiet: bool = typer.Option(False, "--quiet", "-q"),
        debug: bool = typer.Option(False, "--debug", help="On failure, show the full Python traceback")):
    """Process one capture into result.json + plan.png."""
    from roomscan.pipeline import InputError
    from roomscan.pipeline import run as run_pipeline

    out = out or Path("runs") / (capture.stem if capture.suffix.lower() == ".zip" else capture.name or "capture")
    t = time.time()
    try:
        res = run_pipeline(capture, out, tier=tier, stride=stride, drift=drift, damage=not no_damage,
                           use_cache=not no_cache, progress=not quiet)
    except KeyboardInterrupt:
        typer.echo("error: interrupted", err=True)
        raise typer.Exit(130)
    except InputError as e:
        if debug:
            raise
        typer.echo(f"error: {_one_line(e)}", err=True)
        raise typer.Exit(2)
    except Exception as e:  # a bug or an exhausted machine, not the user's input
        if debug:
            raise
        log = _save_traceback(out)
        typer.echo(f"error: processing {capture.name} failed ({type(e).__name__}: {_one_line(e)[:300] or 'no message'}); "
                   f"traceback saved to {log}", err=True)
        raise typer.Exit(1)
    from roomscan.export.sheet import write_sheet
    write_sheet(res, Path(out) / "result.xlsx")  # the same result as a spreadsheet
    typer.echo(_summary(res, out, time.time() - t))


@app.command()
def schema(out: Path = typer.Option(Path("schema/output.schema.json"))):
    """Write the JSON schema of the output contract."""
    from roomscan.schema import Output

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(Output.model_json_schema(), indent=2))
    typer.echo(f"wrote {out}")


if __name__ == "__main__":
    app()
