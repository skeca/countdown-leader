"""Write the README download table from the release matrix in media_spec.py.

    .venv/bin/python docs/download_table.py            # rewrite README.md
    .venv/bin/python docs/download_table.py --check    # fail if README.md is stale
    .venv/bin/python docs/download_table.py --release  # also check every link
                                                       # against the latest release

Links use releases/latest/download/<file>, so they follow each new release as
long as the filenames stay the same; --release catches one that doesn't.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from media_spec import ACTIVE_ASPECT, RATES, SIZES, STYLES, active_rect  # noqa: E402

README = ROOT / "README.md"
REPO = "skeca/countdown-leader"
BASE = f"https://github.com/{REPO}/releases/latest/download"
START, END = "<!-- downloads:start -->", "<!-- downloads:end -->"

ORDER = ["hd", "hd-scope", "uhd", "uhd-scope",
         "2k-full", "2k-flat", "2k-scope", "4k-full", "4k-flat", "4k-scope"]
PICTURE = {"full": "DCI full container", "flat": "DCI Flat, 1.85", "scope": "DCI Scope, 2.39"}
RATE_TEXT = {"2397": "23.976", "24": "24", "25": "25"}


def picture(raster: str) -> str:
    w, h = SIZES[raster]
    if raster in ACTIVE_ASPECT:
        x0, y0, x1, y1 = active_rect(w, h, ACTIVE_ASPECT[raster])
        return f"{ACTIVE_ASPECT[raster]} in {x1 - x0}×{y1 - y0}"
    if raster.startswith(("2k-", "4k-")):
        return PICTURE[raster.split("-", 1)[1]]
    return "full frame"


def filenames() -> list[str]:
    return [f"{s}_{r}_{rate}.mov" for r in ORDER for s in STYLES for rate in RATES]


def table() -> str:
    assert sorted(ORDER) == sorted(SIZES), "ORDER is out of step with media_spec.SIZES"
    rows = ["| Raster | Size | Picture | " + " | ".join(STYLES) + " |",
            "|---|---|---|" + "---|" * len(STYLES)]
    for r in ORDER:
        w, h = SIZES[r]
        cells = [" · ".join(f"[{RATE_TEXT[rate]}]({BASE}/{s}_{r}_{rate}.mov)" for rate in RATES)
                 for s in STYLES]
        rows.append(f"| `{r}` | {w}×{h} | {picture(r)} | " + " | ".join(cells) + " |")
    return "\n".join(rows)


def render(text: str) -> str:
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{table()}\n{END}{tail}"


def release_assets() -> set[str]:
    out = subprocess.run(["gh", "release", "view", "--repo", REPO, "--json", "assets"],
                         check=True, capture_output=True, text=True).stdout
    return {a["name"] for a in json.loads(out)["assets"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true", help="fail if README.md is stale")
    ap.add_argument("--release", action="store_true", help="check links against the latest release")
    args = ap.parse_args()

    if args.release:
        missing = sorted(set(filenames()) - release_assets())
        if missing:
            print("not in the latest release:", *missing, sep="\n  ", file=sys.stderr)
            return 1
        print(f"all {len(filenames())} links resolve to latest-release assets")

    current = README.read_text()
    new = render(current)
    if args.check:
        if new != current:
            print("README.md download table is stale; run docs/download_table.py", file=sys.stderr)
            return 1
        return 0
    if new != current:
        README.write_text(new)
        print("README.md updated")
    return 0


if __name__ == "__main__":
    sys.exit(main())
