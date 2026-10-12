"""Build the bilingual name gazetteer data files (dev-only; uses the network).

Sources (both CC0, pinned by SHA-256):
- SSA baby names 1880-2017, via the CC0 R package hadley/babynames
  (data/babynames.rda at commit 4391c25e). ssa.gov blocks scripted downloads.
- US Census 2010 surnames (names with 100+ occurrences, with % Hispanic).

- US Census 2023 Gazetteer places for North Carolina (public domain): city,
  town, village, and CDP names.

Writes src/localdeid/data/first_names.tsv (name, total count, female share),
src/localdeid/data/surnames.tsv (name, count, % Hispanic), and
src/localdeid/data/nc_places.txt. Run:

    uv run --no-project --with pyreadr --with pandas python tools/build_gazetteer.py
"""

from __future__ import annotations

import hashlib
import io
import re
import sys
import unicodedata
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "src" / "localdeid" / "data"
SOURCES = {
    "babynames.rda": (
        "https://raw.githubusercontent.com/hadley/babynames/4391c25ea10b8b0589cdbab63067de3bc8b3a628/data/babynames.rda",
        "1d5c601fa3c5177f4d9edb4c8c2f08fddc8d9bf04372b8a40dc6aecf92bfa324",
    ),
    "nc_places.txt": (
        "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_gaz_place_37.txt",
        "adc6e5d074e8ec2264c027e507d81bf04a725b2df46c440d14a1540ce2fe1492",
    ),
    "census2010.zip": (
        "https://www2.census.gov/topics/genealogy/2010surnames/names.zip",
        "117c41cb4668727b7627b2845b6df3f83eb2a22a1813f42c0ff4bdcab86de135",
    ),
}
MIN_FIRST = 300  # total births 1940-2017 for a first name to be listed
MIN_SURNAME = 1000  # occurrences in the 2010 census


def fetch(name: str) -> bytes:
    url, want = SOURCES[name]
    data = urllib.request.urlopen(url).read()  # noqa: S310 (fixed https URLs)
    digest = hashlib.sha256(data).hexdigest()
    if want and digest != want:
        sys.exit(f"{name}: sha256 {digest} != {want}")
    print(f"{name}: {len(data)} bytes sha256 {digest}")
    return data


def main() -> None:
    import pandas as pd
    import pyreadr

    tmp = ROOT / "harness" / "out" / "gazetteer_src"
    tmp.mkdir(parents=True, exist_ok=True)
    DATA.mkdir(parents=True, exist_ok=True)
    rda = tmp / "babynames.rda"
    rda.write_bytes(fetch("babynames.rda"))
    df = pyreadr.read_r(str(rda))["babynames"]
    df = df[df["year"] >= 1940]
    g = df.groupby(["name", "sex"])["n"].sum().unstack(fill_value=0)
    g["total"] = g.get("F", 0) + g.get("M", 0)
    g = g[g["total"] >= MIN_FIRST]
    g["female"] = (g["F"] / g["total"]).round(3)
    rows = sorted(((n, int(t), float(f)) for n, t, f in zip(g.index, g["total"], g["female"])),
                  key=lambda r: -r[1])
    with (DATA / "first_names.tsv").open("w", encoding="utf-8") as f:
        f.write("# SSA baby names 1940-2017 (CC0, via hadley/babynames 4391c25e): name, births, female share\n")
        for n, t, fem in rows:
            f.write(f"{unicodedata.normalize('NFC', n)}\t{t}\t{fem}\n")
    print(f"first names: {len(rows)}")

    z = zipfile.ZipFile(io.BytesIO(fetch("census2010.zip")))
    csv_name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
    s = pd.read_csv(z.open(csv_name), keep_default_na=False)
    s = s[s["name"] != "ALL OTHER NAMES"]
    s["count"] = s["count"].astype(int)
    s = s[s["count"] >= MIN_SURNAME]
    pct = pd.to_numeric(s["pcthispanic"].replace("(S)", ""), errors="coerce").fillna(-1)
    with (DATA / "surnames.tsv").open("w", encoding="utf-8") as f:
        f.write("# US Census 2010 surnames (CC0): name, count, percent Hispanic (-1 = suppressed)\n")
        for n, c, h in zip(s["name"], s["count"], pct):
            f.write(f"{str(n).title()}\t{c}\t{h}\n")
    print(f"surnames: {len(s)}")

    places = set()
    for line in fetch("nc_places.txt").decode("utf-8").splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) > 3:
            name = re.sub(r"\s+(?:city|town|village|CDP)$", "", cols[3].strip())
            places.add(unicodedata.normalize("NFC", name))
    with (DATA / "nc_places.txt").open("w", encoding="utf-8") as f:
        f.write("# US Census 2023 Gazetteer, North Carolina places (public domain)\n")
        for name in sorted(places):
            f.write(name + "\n")
    print(f"NC places: {len(places)}")


if __name__ == "__main__":
    main()
