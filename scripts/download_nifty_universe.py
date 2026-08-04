"""Download NIFTY 50, 100, 200, and 500 constituent CSVs from NSE Indices.

Each CSV is downloaded from the official niftyindices.com source and stored
locally in the ``Dependencies`` directory (the same folder the project already
keeps instrument master CSVs in).

Usage:
    .venv/bin/python scripts/download_nifty_universe.py
    .venv/bin/python scripts/download_nifty_universe.py --dest Dependencies
    .venv/bin/python scripts/download_nifty_universe.py --only NIFTY50,NIFTY500
"""
from __future__ import annotations

import argparse
import io
from pathlib import Path

import pandas as pd
import urllib.request

# Official NSE Indices constituent CSVs.
UNIVERSE_URLS = {
    "NIFTY50":  "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "NIFTY100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "NIFTY200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "NIFTY500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}


def _download_csv(url: str) -> pd.DataFrame:
    """Download a single constituent CSV and return it as a DataFrame."""
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    return pd.read_csv(io.StringIO(text))


def download_universe(name: str) -> pd.DataFrame:
    """Download one index constituent CSV; raise KeyError if name is unknown."""
    url = UNIVERSE_URLS[name.upper()]
    return _download_csv(url)


def save_csv(df: pd.DataFrame, path: Path) -> Path:
    """Write the DataFrame to *path* (creating parents) and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)
    return path


def download_all(
    dest: str = "Dependencies",
    only: list[str] | None = None,
) -> dict[str, Path]:
    """Download every requested universe CSV into *dest*.

    Returns a mapping of index name -> local CSV path.
    """
    names = [n.upper() for n in only] if only else list(UNIVERSE_URLS)
    dest_dir = Path(dest)
    out: dict[str, Path] = {}
    for name in names:
        if name not in UNIVERSE_URLS:
            raise KeyError(f"Unknown universe {name!r}; available: {sorted(UNIVERSE_URLS)}")
        print(f"Downloading {name}…")
        df = download_universe(name)
        # Normalize: strip whitespace from string columns and drop fully-empty rows.
        for col in df.select_dtypes(include=["object", "str"]).columns:
            df[col] = df[col].astype(str).str.strip()
        df = df.dropna(how="all")
        path = save_csv(df, dest_dir / f"{name.lower()}_list.csv")
        out[name] = path
        print(f"  {len(df)} constituents → {path}")
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Download NIFTY 50/100/200/500 constituent CSVs")
    p.add_argument("--dest", default="Dependencies", help="destination directory (default: Dependencies)")
    p.add_argument("--only", default=None, help="comma-separated subset, e.g. NIFTY50,NIFTY500")
    args = p.parse_args()

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    result = download_all(dest=args.dest, only=only)
    print(f"\nDone: {len(result)} files saved.")


if __name__ == "__main__":
    main()
