import argparse
import sys

from . import __version__


def main() -> None:
    p = argparse.ArgumentParser(prog="inkwake", description="Ink: meeting assistant (captions, translation, report)")
    p.add_argument("--no-detect", action="store_true", help="don't watch for meetings; start manually from the bubble")
    p.add_argument("--version", action="version", version=__version__)
    args = p.parse_args()

    from .app import run

    sys.exit(run(detect=not args.no_detect))


if __name__ == "__main__":
    main()
