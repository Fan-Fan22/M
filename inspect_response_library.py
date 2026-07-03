#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse
from pathlib import Path
import numpy as np


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("library_npz")
    args = ap.parse_args()
    p = Path(args.library_npz)
    z = np.load(p, allow_pickle=True)
    print("file:", p)
    for k in ["r_grid_cm", "alpha_grid_deg", "beta_grid_deg", "energy_windows_mev", "source_energy_mev", "pixel_shape"]:
        if k in z:
            arr = z[k]
            print(f"{k}: shape={arr.shape}, value/head={arr if arr.size <= 20 else arr[:10]}")
    H = z["response"]
    R = z["relerr"] if "relerr" in z else None
    print("response shape:", H.shape)
    print("response dtype:", H.dtype)
    print("response min/max/sum:", float(np.nanmin(H)), float(np.nanmax(H)), float(np.nansum(H)))
    if R is not None:
        print("relerr min/median/max:", float(np.nanmin(R)), float(np.nanmedian(R)), float(np.nanmax(R)))
        print("relerr > 0.2 count:", int(np.sum(R > 0.2)))
    # Show center-angle summaries if grids contain alpha=0 beta=0.
    a = z["alpha_grid_deg"]
    b = z["beta_grid_deg"]
    ia0 = int(np.argmin(np.abs(a)))
    ib0 = int(np.argmin(np.abs(b)))
    center_sum_by_r = H[:, ia0, ib0, :, :].sum(axis=(1,2))
    print("center alpha/beta indices:", ia0, ib0, "alpha/beta=", float(a[ia0]), float(b[ib0]))
    print("center response sum by r:")
    for r, s in zip(z["r_grid_cm"], center_sum_by_r):
        print(f"  r={float(r):7.2f} cm  sum={float(s):.8e}")

if __name__ == "__main__":
    main()
