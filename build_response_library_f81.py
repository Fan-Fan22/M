#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Build a Cs-137 local camera response library for MURA24m:
    H_E662(r, alpha, beta, window, pixel)

Coordinate convention inherited from the supplied MURA24m.i:
    Camera looks along local -z.
    Given (r, alpha, beta):
        d = r / sqrt(1 + tan(alpha)^2 + tan(beta)^2)
        x = d*tan(alpha), y = d*tan(beta), z = -d

Default tally:
    F18:P pulse-height tally over pixel cells 1001..1361.
    E18 0.50 0.80
    Parsed window: (0.50,0.80] MeV.

Output .npz keys:
    r_grid_cm, alpha_grid_deg, beta_grid_deg, energy_windows_mev, source_energy_mev
    response: float32, shape=(Nr, Nalpha, Nbeta, Nwin, 361)
    relerr:   float32, same shape
    source_pos_cm: float32, shape=(Nr, Nalpha, Nbeta, 3)
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np

FLOAT_RE = re.compile(r"[+-]?(?:(?:\d+\.\d*)|(?:\.\d+)|(?:\d+))(?:[Ee][+-]?\d+)?")
SDEF_RE = re.compile(r"^\s*sdef\b", re.IGNORECASE)
NPS_RE = re.compile(r"^\s*nps\b", re.IGNORECASE)
MODE_RE = re.compile(r"^\s*mode\b", re.IGNORECASE)

@dataclass(frozen=True)
class Job:
    index: int
    ir: int
    ia: int
    ib: int
    r_cm: float
    alpha_deg: float
    beta_deg: float
    x_cm: float
    y_cm: float
    z_cm: float
    name: str
    job_dir: Path
    input_path: Path
    output_path: Path


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(p: str | os.PathLike[str], base_dir: Path) -> Path:
    q = Path(p)
    if q.is_absolute():
        return q
    return (base_dir / q).resolve()


def arange_inclusive(start: float, stop: float, step: float) -> np.ndarray:
    n = int(round((stop - start) / step))
    arr = start + step * np.arange(n + 1)
    arr = arr[arr <= stop + 1e-8]
    return np.round(arr, 10)


def grid_from_cfg(cfg: Dict[str, Any], prefix: str, suffix: str) -> np.ndarray:
    # Accept r_grid_cm, alpha_grid_deg, beta_grid_deg or start/stop/step keys.
    list_keys = [f"{prefix}_grid{suffix}", f"{prefix}{suffix}", f"{prefix}_grid", prefix]
    for k in list_keys:
        if k in cfg and isinstance(cfg[k], list):
            return np.asarray(cfg[k], dtype=float)
    start_keys = [f"{prefix}{suffix}_start", f"{prefix}_start{suffix}", f"{prefix}_start"]
    stop_keys = [f"{prefix}{suffix}_stop", f"{prefix}_stop{suffix}", f"{prefix}_stop"]
    step_keys = [f"{prefix}{suffix}_step", f"{prefix}_step{suffix}", f"{prefix}_step"]
    def first(keys):
        for k in keys:
            if k in cfg:
                return float(cfg[k])
        return None
    start = first(start_keys)
    stop = first(stop_keys)
    step = first(step_keys)
    if start is None or stop is None or step is None:
        raise KeyError(f"Missing grid for {prefix}; provide list or start/stop/step")
    if step <= 0:
        raise ValueError(f"{prefix} step must be positive")
    return arange_inclusive(start, stop, step)


def source_from_rab(r: float, alpha_deg: float, beta_deg: float) -> Tuple[float, float, float]:
    ta = math.tan(math.radians(alpha_deg))
    tb = math.tan(math.radians(beta_deg))
    d = r / math.sqrt(1.0 + ta * ta + tb * tb)
    return d * ta, d * tb, -d


def fmt(x: float) -> str:
    if abs(x) < 1e-12:
        return "0"
    return f"{x:.8g}"


def render_template(text: str, pos: Sequence[float], energy_mev: float, nps: int | str) -> str:
    # Replace placeholders if present, then force SDEF/MODE/NPS.
    repl = {
        "{src_x}": fmt(pos[0]), "{src_y}": fmt(pos[1]), "{src_z}": fmt(pos[2]),
        "{source_x}": fmt(pos[0]), "{source_y}": fmt(pos[1]), "{source_z}": fmt(pos[2]),
        "{energy_mev}": fmt(energy_mev), "{source_energy_mev}": fmt(energy_mev),
        "{nps}": str(nps), "{NPS}": str(nps),
    }
    for k, v in repl.items():
        text = text.replace(k, v)

    out = []
    sdef_done = False
    mode_done = False
    nps_done = False
    for line in text.splitlines():
        if SDEF_RE.match(line):
            out.append(f"sdef  POS={fmt(pos[0])} {fmt(pos[1])} {fmt(pos[2])}  ERG={fmt(energy_mev)}  PAR=2")
            sdef_done = True
        elif MODE_RE.match(line):
            out.append("mode p")
            mode_done = True
        elif NPS_RE.match(line):
            out.append(f"nps  {nps}")
            nps_done = True
        else:
            out.append(line)
    if not mode_done:
        out.append("mode p")
    if not sdef_done:
        out.append(f"sdef  POS={fmt(pos[0])} {fmt(pos[1])} {fmt(pos[2])}  ERG={fmt(energy_mev)}  PAR=2")
    if not nps_done:
        out.append(f"nps  {nps}")
    result = "\n".join(out) + "\n"
    unresolved = sorted(set(re.findall(r"\{[A-Za-z0-9_]+\}", result)))
    if unresolved:
        raise ValueError("Unresolved placeholders: " + ", ".join(unresolved))
    return result


def make_jobs(cfg: Dict[str, Any], cfg_dir: Path) -> Tuple[List[Job], np.ndarray, np.ndarray, np.ndarray]:
    r_grid = grid_from_cfg(cfg, "r", "_cm")
    a_grid = grid_from_cfg(cfg, "alpha", "_deg")
    b_grid = grid_from_cfg(cfg, "beta", "_deg")
    work_dir = resolve_path(cfg.get("work_dir", "../jobs/jobs_E662_w050_080"), cfg_dir)
    jobs: List[Job] = []
    idx = 0
    for ir, r in enumerate(r_grid):
        for ia, a in enumerate(a_grid):
            for ib, b in enumerate(b_grid):
                x, y, z = source_from_rab(float(r), float(a), float(b))
                name = (
                    f"j{idx:05d}_ir{ir:02d}_ia{ia:02d}_ib{ib:02d}"
                    f"_r{r:.3f}_a{a:+.3f}_b{b:+.3f}"
                ).replace("+", "p").replace("-", "m").replace(".", "p")
                jd = work_dir / name
                jobs.append(Job(idx, ir, ia, ib, float(r), float(a), float(b), x, y, z, name, jd, jd / (name + ".i"), jd / (name + ".o")))
                idx += 1
    return jobs, r_grid, a_grid, b_grid


def filter_jobs(jobs: Sequence[Job], start_index: Optional[int], end_index: Optional[int]) -> List[Job]:
    out = []
    for j in jobs:
        if start_index is not None and j.index < start_index:
            continue
        if end_index is not None and j.index > end_index:
            continue
        out.append(j)
    return out


def write_manifest(path: Path, jobs: Sequence[Job]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["index", "ir", "ia", "ib", "r_cm", "alpha_deg", "beta_deg", "x_cm", "y_cm", "z_cm", "input", "output"])
        for j in jobs:
            w.writerow([j.index, j.ir, j.ia, j.ib, j.r_cm, j.alpha_deg, j.beta_deg, j.x_cm, j.y_cm, j.z_cm, str(j.input_path), str(j.output_path)])


def generate_inputs(cfg: Dict[str, Any], cfg_dir: Path, jobs: Sequence[Job]) -> None:
    template = resolve_path(cfg["template_input"], cfg_dir)
    text = template.read_text(encoding="utf-8", errors="ignore")
    energy = float(cfg.get("source_energy_mev", cfg.get("energy_mev", 0.662)))
    nps = cfg.get("nps", "1e7")
    overwrite = bool(cfg.get("overwrite_inputs", True))
    for k, job in enumerate(jobs, 1):
        job.job_dir.mkdir(parents=True, exist_ok=True)
        if job.input_path.exists() and not overwrite:
            continue
        job.input_path.write_text(render_template(text, [job.x_cm, job.y_cm, job.z_cm], energy, nps), encoding="utf-8")
        if k == 1 or k == len(jobs) or k % max(1, len(jobs)//10) == 0:
            print(f"generated [{k}/{len(jobs)}] {job.input_path}")


def build_command(cfg: Dict[str, Any], job: Job) -> List[str]:
    mcnp_exe = str(cfg.get("mcnp_exe", "mcnp6"))
    if cfg.get("use_mpiexec", False):
        cmd = [str(cfg.get("mpiexec_exe", "mpiexec")), str(cfg.get("mpi_np_flag", "-n")), str(cfg.get("mpi_np", 1))]
        cmd += [str(x) for x in cfg.get("mpiexec_args", [])]
        cmd += [mcnp_exe]
    else:
        cmd = [mcnp_exe]
    cmd += [f"i={job.input_path.name}", f"o={job.output_path.name}"]
    cmd += [str(x) for x in cfg.get("mcnp_extra_args", [])]
    return cmd


def run_one(job: Job, cfg: Dict[str, Any]) -> Tuple[str, int]:
    if job.output_path.exists() and not bool(cfg.get("overwrite_outputs", False)):
        return job.name, 0
    for p in job.job_dir.glob("runtp*"):
        try:
            p.unlink()
        except OSError:
            pass
    cp = subprocess.run(build_command(cfg, job), cwd=str(job.job_dir), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors="replace")
    (job.job_dir / (job.name + ".log")).write_text(cp.stdout or "", encoding="utf-8", errors="ignore")
    return job.name, int(cp.returncode)


def run_jobs(jobs: Sequence[Job], cfg: Dict[str, Any]) -> None:
    max_workers = int(cfg.get("max_workers", 1))
    if max_workers <= 1:
        for k, job in enumerate(jobs, 1):
            print(f"[{k}/{len(jobs)}] running {job.name}")
            _, rc = run_one(job, cfg)
            if rc != 0:
                raise RuntimeError(f"MCNP failed for {job.name}; see {job.job_dir}")
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as ex:
            futs = {ex.submit(run_one, job, cfg): job for job in jobs}
            done = 0
            for fut in as_completed(futs):
                done += 1
                job = futs[fut]
                _, rc = fut.result()
                print(f"[{done}/{len(jobs)}] finished {job.name} rc={rc}")
                if rc != 0:
                    raise RuntimeError(f"MCNP failed for {job.name}; see {job.job_dir}")


def extract_tally_section(text: str, tally_number: int) -> str:
    # MCNP output formats vary. Start at tally header if possible.
    pat = re.compile(rf"(?im)^\s*1\s*tally\s+{tally_number}\b|^\s*1tally\s+{tally_number}\b|^\s*tally\s+{tally_number}\b")
    m = pat.search(text)
    if not m:
        return text
    start = m.start()
    next_pat = re.compile(r"(?im)^\s*1\s*tally\s+\d+\b|^\s*1tally\s+\d+\b|^\s*tally\s+\d+\b")
    m2 = next_pat.search(text, m.end())
    end = m2.start() if m2 else len(text)
    return text[start:end]


def numeric_rows(block: str) -> List[Tuple[float, float, float, str]]:
    rows = []
    for line in block.splitlines():
        if "total" in line.lower():
            continue
        vals = FLOAT_RE.findall(line)
        if len(vals) >= 3:
            try:
                e, val, err = float(vals[0]), float(vals[1]), float(vals[2])
            except ValueError:
                continue
            if math.isfinite(e) and math.isfinite(val) and math.isfinite(err) and e >= 0 and err >= 0:
                rows.append((e, val, err, line))
    return rows


def parse_one_cell_block(block: str, upper_edges: Sequence[float], tol: float = 2e-3) -> Optional[Tuple[np.ndarray, np.ndarray]]:
    rows = numeric_rows(block)
    vals = []
    errs = []
    for edge in upper_edges:
        candidates = [(abs(e - edge), val, err) for e, val, err, _ in rows if abs(e - edge) <= max(tol, abs(edge) * 1e-3)]
        if not candidates:
            return None
        _, val, err = min(candidates, key=lambda x: x[0])
        vals.append(val)
        errs.append(err)
    return np.asarray(vals, dtype=float), np.asarray(errs, dtype=float)


def parse_f8_multiwin(output_path: Path, tally_number: int, cell_ids: Sequence[int], energy_windows: Sequence[Sequence[float]]) -> Tuple[np.ndarray, np.ndarray]:
    text = output_path.read_text(encoding="utf-8", errors="replace")
    section = extract_tally_section(text, tally_number)
    upper_edges = [float(w[1]) for w in energy_windows]
    nwin = len(upper_edges)
    npix = len(cell_ids)
    values = np.zeros((nwin, npix), dtype=np.float64)
    relerr = np.full((nwin, npix), np.nan, dtype=np.float64)
    wanted = [int(c) for c in cell_ids]
    parsed: Dict[int, Tuple[np.ndarray, np.ndarray]] = {}

    # Preferred output form: blocks starting with "cell 1001".
    cell_pat = re.compile(r"(?im)^\s*cell\s+([0-9]+)\b")
    matches = list(cell_pat.finditer(section))
    if matches:
        for idx, m in enumerate(matches):
            cell = int(m.group(1))
            if cell not in wanted:
                continue
            b0 = m.end()
            b1 = matches[idx + 1].start() if idx + 1 < len(matches) else len(section)
            res = parse_one_cell_block(section[b0:b1], upper_edges)
            if res is not None:
                parsed[cell] = res

    # Fallback: blocks starting with the cell number itself.
    if len(parsed) < npix:
        for cell in wanted:
            if cell in parsed:
                continue
            pat = re.compile(rf"(?m)^\s*{cell}\b")
            m = pat.search(section)
            if not m:
                continue
            block = "\n".join(section[m.start():].splitlines()[:80])
            res = parse_one_cell_block(block, upper_edges)
            if res is not None:
                parsed[cell] = res

    missing = [c for c in wanted if c not in parsed]
    if missing:
        dbg = output_path.with_suffix(output_path.suffix + f".tally{tally_number}.debug.txt")
        dbg.write_text(section, encoding="utf-8", errors="replace")
        raise ValueError(f"Parsed {npix-len(missing)}/{npix} F{tally_number} cell blocks in {output_path}; missing first={missing[:10]}. Debug: {dbg}")

    for i, cell in enumerate(wanted):
        v, e = parsed[cell]
        values[:, i] = v
        relerr[:, i] = e
    return values, relerr


def build_library(cfg: Dict[str, Any], cfg_dir: Path, jobs: Sequence[Job], r_grid: np.ndarray, a_grid: np.ndarray, b_grid: np.ndarray) -> None:
    expected_bins = int(cfg.get("expected_bins", 361))
    first_cell = int(cfg.get("first_pixel_cell", 1001))
    cell_ids = [int(c) for c in cfg.get("cell_ids", list(range(first_cell, first_cell + expected_bins)))]
    windows = cfg.get("energy_windows_mev", [[0.50, 0.80]])
    windows = [[float(x[0]), float(x[1])] for x in windows]
    tally_number = int(cfg.get("tally_number", 18))
    nwin = len(windows)
    # Use NaN for response by default so missing/unparsed jobs are not
    # accidentally treated as physical zero response in partial libraries.
    H = np.full((len(r_grid), len(a_grid), len(b_grid), nwin, expected_bins), np.nan, dtype=np.float32)
    R = np.full_like(H, np.nan, dtype=np.float32)
    pos = np.zeros((len(r_grid), len(a_grid), len(b_grid), 3), dtype=np.float32)

    allow_missing = bool(cfg.get("allow_missing_outputs", False))
    allow_parse_errors = bool(cfg.get("allow_parse_errors", False))
    parsed_jobs = 0
    missing_jobs = 0
    failed_parse_jobs = 0

    for k, job in enumerate(jobs, 1):
        pos[job.ir, job.ia, job.ib, :] = [job.x_cm, job.y_cm, job.z_cm]
        if not job.output_path.exists():
            if allow_missing:
                missing_jobs += 1
                if missing_jobs <= 10:
                    print(f"missing output, skipped: {job.output_path}")
                continue
            raise FileNotFoundError(job.output_path)
        if k == 1 or k == len(jobs) or k % max(1, len(jobs)//20) == 0:
            print(f"parsing [{k}/{len(jobs)}] {job.name}")
        try:
            val, err = parse_f8_multiwin(job.output_path, tally_number, cell_ids, windows)
        except Exception as exc:
            if allow_parse_errors:
                failed_parse_jobs += 1
                print(f"parse failed, skipped: {job.output_path} :: {exc}")
                continue
            raise
        H[job.ir, job.ia, job.ib, :, :] = val.astype(np.float32)
        R[job.ir, job.ia, job.ib, :, :] = err.astype(np.float32)
        parsed_jobs += 1

    out = resolve_path(cfg.get("library_output", "../outputs/H_E662_w050_080_MURA24m.npz"), cfg_dir)
    out.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        out,
        r_grid_cm=np.asarray(r_grid, dtype=np.float32),
        alpha_grid_deg=np.asarray(a_grid, dtype=np.float32),
        beta_grid_deg=np.asarray(b_grid, dtype=np.float32),
        energy_windows_mev=np.asarray(windows, dtype=np.float32),
        source_energy_mev=np.asarray(float(cfg.get("source_energy_mev", 0.662)), dtype=np.float32),
        response=H,
        relerr=R,
        source_pos_cm=pos,
        tally_number=np.asarray(tally_number),
        expected_bins=np.asarray(expected_bins),
        cell_ids=np.asarray(cell_ids, dtype=np.int32),
        pixel_shape=np.asarray([19, 19], dtype=np.int32),
        coordinate_system=np.asarray("camera forward = local -z"),
        config_json=np.asarray(json.dumps(cfg, ensure_ascii=False)),
        parsed_jobs=np.asarray(parsed_jobs, dtype=np.int32),
        missing_jobs=np.asarray(missing_jobs, dtype=np.int32),
        failed_parse_jobs=np.asarray(failed_parse_jobs, dtype=np.int32),
    )
    print(f"wrote {out}")
    print(f"response shape = {H.shape}")
    print(f"parsed jobs = {parsed_jobs}, missing jobs = {missing_jobs}, parse failures = {failed_parse_jobs}")
    print(f"total response sum = {float(np.nansum(H)):.8e}")


def print_summary(cfg_path: Path, cfg: Dict[str, Any], cfg_dir: Path, jobs: Sequence[Job], r: np.ndarray, a: np.ndarray, b: np.ndarray) -> None:
    print("config =", cfg_path)
    print("template_input =", resolve_path(cfg["template_input"], cfg_dir))
    print("work_dir =", resolve_path(cfg.get("work_dir", "../jobs/jobs_E662_w050_080"), cfg_dir))
    print("library_output =", resolve_path(cfg.get("library_output", "../outputs/H_E662_w050_080_MURA24m.npz"), cfg_dir))
    print("source_energy_mev =", cfg.get("source_energy_mev", 0.662))
    print("energy_windows_mev =", cfg.get("energy_windows_mev", [[0.50, 0.80]]))
    print("r_grid_cm =", r.tolist())
    print("alpha_grid_deg =", (float(a[0]), float(a[-1]), len(a)))
    print("beta_grid_deg =", (float(b[0]), float(b[-1]), len(b)))
    print("estimated jobs =", len(jobs))
    if jobs:
        for j in [jobs[0], jobs[len(jobs)//2], jobs[-1]]:
            print(f"example index={j.index}: r={j.r_cm}, alpha={j.alpha_deg}, beta={j.beta_deg}, POS={j.x_cm:.6g} {j.y_cm:.6g} {j.z_cm:.6g}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--dry-run", action="store_true", help="Print grid/job summary only; no input generation.")
    ap.add_argument("--write-manifest", action="store_true", help="Write source-points/job manifest CSV and exit unless other actions are requested.")
    ap.add_argument("--generate-only", action="store_true", help="Generate MCNP input files only.")
    ap.add_argument("--parse-only", action="store_true", help="Parse existing MCNP outputs into the response library.")
    ap.add_argument("--start-index", type=int, default=None, help="Only process jobs with index >= this value.")
    ap.add_argument("--end-index", type=int, default=None, help="Only process jobs with index <= this value.")
    args = ap.parse_args()

    cfg_path = Path(args.config).resolve()
    cfg_dir = cfg_path.parent
    cfg = load_json(cfg_path)
    jobs_all, r, a, b = make_jobs(cfg, cfg_dir)
    jobs = filter_jobs(jobs_all, args.start_index, args.end_index)
    print_summary(cfg_path, cfg, cfg_dir, jobs_all, r, a, b)
    if len(jobs) != len(jobs_all):
        print(f"selected jobs = {len(jobs)} / {len(jobs_all)}")

    manifest_path = resolve_path(cfg.get("manifest_output", "../manifests/source_points_E662_w050_080.csv"), cfg_dir)
    if args.dry_run:
        return
    if args.write_manifest:
        write_manifest(manifest_path, jobs_all)
        print(f"wrote manifest {manifest_path}")
        if not args.generate_only and not args.parse_only:
            return
    if not args.parse_only:
        generate_inputs(cfg, cfg_dir, jobs)
    if args.generate_only:
        return
    if not args.parse_only and bool(cfg.get("run_mcnp", True)):
        run_jobs(jobs, cfg)
    elif not args.parse_only:
        print("run_mcnp=false; skip MCNP run. Use --parse-only after MCNP outputs exist.")
        return
    # Library parse requires the full job set, not a chunk, unless the user intentionally parses a chunk.
    if args.start_index is not None or args.end_index is not None:
        print("WARNING: parsing only selected jobs will not fill the full response array. Usually run parse-only without start/end.")
    build_library(cfg, cfg_dir, jobs, r, a, b)

if __name__ == "__main__":
    main()
