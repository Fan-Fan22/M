#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Patch MURA24m.i so it contains an F18 pulse-height tally for the 19x19 LaBr3 pixels.
The original MURA24m.i contains F14/F16 cell tallies; this script appends/replaces F18/E18.
"""
from __future__ import annotations
import argparse
import re
from pathlib import Path


def make_cell_lines(tally_number: int = 18, first_cell: int = 1001, n_pixel: int = 361) -> str:
    cells = list(range(first_cell, first_cell + n_pixel))
    lines = [f"f{tally_number}:p  " + "  ".join(str(c) for c in cells[:10])]
    for i in range(10, len(cells), 10):
        lines.append("     " + "  ".join(str(c) for c in cells[i:i+10]))
    return "\n".join(lines)


def patch_text(text: str, tally_number: int, energy_edges_mev: list[float]) -> str:
    fcard = make_cell_lines(tally_number=tally_number)
    ecard = f"e{tally_number} " + " ".join(f"{e:.8g}" for e in energy_edges_mev)
    block = (
        "c\n"
        "c F18 pulse-height tally for Cs-137 local camera response library.\n"
        "c Pixel cells: 1001--1361, deposited energy window: (0.50,0.80] MeV.\n"
        f"{fcard}\n"
        f"{ecard}\n"
        "c\n"
    )

    # Remove existing F18/E18, if any.
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        line = lines[i]
        low = line.strip().lower()
        if re.match(rf"^f{tally_number}\s*:", low) or re.match(rf"^f{tally_number}\b", low):
            i += 1
            while i < len(lines):
                nxt = lines[i].strip().lower()
                if re.match(r"^(c\b|nps\b|mode\b|sdef\b|cut:|tr\d+\b|m\d+\b|f\d+\b|e\d+\b)", nxt):
                    break
                i += 1
            continue
        if re.match(rf"^e{tally_number}\b", low):
            i += 1
            continue
        out.append(line)
        i += 1

    text2 = "\n".join(out) + "\n"
    m = re.search(r"(?im)^\s*nps\b", text2)
    if m:
        text2 = text2[:m.start()] + block + text2[m.start():]
    else:
        text2 += "\n" + block + "nps 1e7\n"
    return text2


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="templates/MURA24m_original.i")
    ap.add_argument("--output", default="templates/MURA24m_f8_E662_w050_080.i")
    ap.add_argument("--tally-number", type=int, default=18)
    ap.add_argument("--energy-edges", nargs="+", type=float, default=[0.50, 0.80])
    args = ap.parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    text = inp.read_text(encoding="utf-8", errors="ignore")
    patched = patch_text(text, args.tally_number, args.energy_edges)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(patched, encoding="utf-8")
    print(f"wrote {out}")
    print(f"tally = F{args.tally_number}:P")
    print("energy edges =", args.energy_edges)

if __name__ == "__main__":
    main()
