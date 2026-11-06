"""Offline, auditable XRD routines. These are not JADE's calculation engine."""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import math
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import numpy as np
from scipy import sparse
from scipy.optimize import curve_fit
from scipy.signal import find_peaks, peak_widths, savgol_filter
from scipy.sparse.linalg import spsolve

ROOT = Path(__file__).resolve().parent.parent
RESULTS = Path(os.environ.get("JADE_RESULTS_DIR", str(ROOT / "results"))).resolve()
NUMBER = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?$")


def finite(value, name, positive=False):
    value = float(value)
    if not math.isfinite(value) or (positive and value <= 0):
        raise ValueError(f"{name} must be finite" + (" and > 0" if positive else ""))
    return value


def clean_json(value):
    if isinstance(value, dict):
        return {str(k): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [clean_json(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if np.isfinite(value) else None
    if isinstance(value, np.integer):
        return int(value)
    return value


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def new_run(kind):
    RESULTS.mkdir(parents=True, exist_ok=True)
    target = RESULTS / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}_{kind}_{uuid4().hex[:10]}"
    target.mkdir()
    return target


def save_json(path, data):
    Path(path).write_text(json.dumps(clean_json(data), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


@dataclass
class Pattern:
    path: Path
    x: np.ndarray
    y: np.ndarray
    metadata: dict


def read_pattern(path: str, x_column=0, y_column=1, skip_rows=0) -> Pattern:
    source = Path(path).expanduser().resolve(strict=True)
    if not source.is_file() or source.stat().st_size > 100_000_000:
        raise ValueError("Input must be a file <= 100 MB")
    if source.suffix.lower() not in {".xy", ".xye", ".csv", ".tsv", ".txt", ".dat", ".mdi"}:
        raise ValueError("Supported: text XY/XYE/CSV/TSV/TXT/DAT and MDI ASCII. Export binary RAW/RD/BRML from JADE first.")
    raw = source.read_bytes()
    if b"\0" in raw[:8192]:
        raise ValueError("Binary input: export a two-column text pattern from JADE")
    for encoding in ("utf-8-sig", "gb18030", "cp1252"):
        try:
            content = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("Cannot decode text data")
    lines = content.splitlines()
    metadata = {"source": str(source), "source_sha256": hashlib.sha256(raw).hexdigest(), "encoding": encoding,
                "x_unit": "2theta_deg", "intensity_unit": "source units", "notes": []}
    if source.suffix.lower() == ".mdi":
        # Older bundled JADE ASCII examples end in DOS Ctrl-Z. This is a file
        # terminator, not a numeric row. Only strip it at the physical end.
        lines = content.rstrip().removesuffix('\x1a').rstrip().splitlines()
        # MDI ASCII: title; start, step, time, anode, wavelength, end, count; packed intensities.
        if len(lines) < 3:
            raise ValueError("Truncated MDI ASCII file")
        fields = lines[1].split()
        if len(fields) < 7:
            raise ValueError("Invalid MDI header")
        start, step, end, count = float(fields[0]), float(fields[1]), float(fields[5]), int(fields[6])
        y = np.asarray([float(v) for row in lines[2:] for v in row.split()], dtype=float)
        # Accept only the exact legacy trailer observed in the bundled MDI files:
        # declared intensities, then -1, optionally zero-padding one packed row.
        # Any other count mismatch still fails rather than discarding real data.
        trailer=y[count:] if count>0 else np.array([])
        if 1<=len(trailer)<=8 and trailer[0]==-1 and np.all(trailer[1:]==0):
            y=y[:count]
            metadata['notes'].append('Legacy MDI -1 terminator and optional zero padding excluded after declared point count')
        if len(y) != count or step <= 0 or not np.isclose(start + step * (count - 1), end, atol=step / 10):
            raise ValueError("MDI count/range mismatch; data were not silently truncated")
        x = start + np.arange(count) * step
        metadata.update(title=lines[0].strip(), wavelength_angstrom=float(fields[4]), anode=fields[3],
                        count_time_seconds=float(fields[2]))
    else:
        if min(x_column, y_column, skip_rows) < 0 or x_column == y_column:
            raise ValueError("Choose distinct nonnegative X/Y column indexes and skip_rows >= 0")
        rows, skipped = [], 0
        for line in lines[skip_rows:]:
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", ";", "!", "//")):
                continue
            values = re.split(r"[,;\s]+", stripped)
            if max(x_column, y_column) < len(values) and NUMBER.fullmatch(values[x_column]) and NUMBER.fullmatch(values[y_column]):
                rows.append((float(values[x_column]), float(values[y_column])))
            else:
                if rows:
                    raise ValueError(f"Non-numeric row after data began: {stripped[:100]}; specify skip_rows/columns or export clean XY")
                skipped += 1
        if not rows:
            raise ValueError("No numeric X/Y rows found")
        x, y = np.asarray(rows, dtype=float).T
        metadata.update(x_column=x_column, y_column=y_column, skip_rows=skip_rows, header_rows_skipped=skipped)
    if len(x) < 5 or len(x) > 2_000_000 or not np.all(np.isfinite(x)) or not np.all(np.isfinite(y)):
        raise ValueError("Need 5..2,000,000 finite points")
    if np.all(np.diff(x) < 0):
        x, y = x[::-1].copy(), y[::-1].copy()
        metadata["notes"].append("Descending scan reversed")
    if np.any(np.diff(x) <= 0) or x[0] <= 0 or x[-1] >= 180:
        raise ValueError("X must be strictly increasing 2theta in degrees, 0 < X < 180 (no duplicates)")
    metadata.update(points=len(x), two_theta_range=[float(x[0]), float(x[-1])],
                    intensity_range=[float(y.min()), float(y.max())],
                    median_step_deg=float(np.median(np.diff(x))))
    return Pattern(source, x, y, metadata)


def bragg(two_theta_deg, wavelength_angstrom):
    wavelength = finite(wavelength_angstrom, "wavelength_angstrom", True)
    x = np.asarray(two_theta_deg, dtype=float)
    if not np.all(np.isfinite(x)) or np.any((x <= 0) | (x >= 180)):
        raise ValueError("2theta must be between 0 and 180 degrees")
    return wavelength / (2 * np.sin(np.deg2rad(x / 2)))


def background_als(y, lam=1e6, asymmetry=0.001, iterations=15):
    lam = finite(lam, "lambda", True)
    if not 0 < asymmetry < 1 or not 1 <= iterations <= 100:
        raise ValueError("Require 0 < asymmetry < 1; 1 <= iterations <= 100")
    n = len(y)
    d = sparse.diags([1., -2., 1.], [0, 1, 2], shape=(n - 2, n), format="csc", dtype=float)
    penalty = lam * d.T @ d
    w = np.ones(n)
    for _ in range(iterations):
        z = spsolve(sparse.diags(w, format="csc") + penalty, w * y)
        w = np.where(y > z, asymmetry, 1 - asymmetry)
    return z


def preprocess(pattern, background="als", als_lambda=1e6, als_asymmetry=0.001):
    if background not in {"als", "none"}:
        raise ValueError("background must be als or none")
    # ALS assumes a uniform sampling grid. Reject instead of silently treating gaps as equal steps.
    if background == "als" and not np.allclose(np.diff(pattern.x), np.median(np.diff(pattern.x)), rtol=0.02, atol=1e-7):
        raise ValueError("ALS requires evenly sampled 2theta. Export/resample onto a uniform grid first.")
    base = background_als(pattern.y, als_lambda, als_asymmetry) if background == "als" else np.zeros_like(pattern.y)
    return base, pattern.y - base


def detect(x, corrected, wavelength, prominence_fraction=0.03, min_distance_deg=0.15, smoothing_points=7):
    if not 0 < prominence_fraction <= 1:
        raise ValueError("prominence_fraction must be in (0,1]")
    min_distance_deg = finite(min_distance_deg, "min_distance_deg", True)
    if smoothing_points != 0 and (smoothing_points < 5 or smoothing_points % 2 != 1 or smoothing_points > len(x)):
        raise ValueError("smoothing_points must be 0 or an odd integer >= 5 and <= point count")
    signal = savgol_filter(corrected, smoothing_points, 3) if smoothing_points else corrected
    span = float(np.ptp(signal))
    if span <= 0:
        return []
    indexes, props = find_peaks(signal, prominence=span * prominence_fraction)
    # Enforce separation in physical angle, including nonuniform grids.
    picked = []
    for k in sorted(range(len(indexes)), key=lambda k: props["prominences"][k], reverse=True):
        if all(abs(x[indexes[k]] - x[indexes[j]]) >= min_distance_deg for j in picked):
            picked.append(k)
    if not picked:
        return []
    picked.sort(key=lambda k: indexes[k])
    indexes = indexes[picked]
    widths = peak_widths(signal, indexes, rel_height=0.5)
    grid = np.arange(len(x))
    fwhm = np.interp(widths[3], grid, x) - np.interp(widths[2], grid, x)
    maximum = max(float(np.max(corrected)), 1e-30)
    return [{"two_theta_deg": float(x[i]), "d_angstrom": float(bragg([x[i]], wavelength)[0]),
             "intensity": float(corrected[i]), "relative_intensity_percent": 100 * float(corrected[i]) / maximum,
             "prominence": float(props["prominences"][k]), "apparent_fwhm_deg": float(width),
             "width_method": "half prominence of detection signal; use fitted FWHM for size/strain"}
            for i, k, width in zip(indexes, picked, fwhm)]


def write_csv(path, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0]) if rows else ["two_theta_deg"]
    with Path(path).open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def plot_patterns(curves, target, xlabel="2theta (deg)", ylabel="Intensity (a.u.)"):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    with plt.rc_context({"font.family": "DejaVu Sans", "svg.fonttype": "none", "font.size": 10}):
        fig, ax = plt.subplots(figsize=(8, 4.5), layout="constrained")
        for x, y, label in curves:
            ax.plot(x, y, lw=1, label=label)
        ax.set(xlabel=xlabel, ylabel=ylabel)
        ax.legend(fontsize=8)
        for ext in ("png", "svg", "pdf"):
            fig.savefig(Path(target).with_suffix("." + ext), dpi=300)
        plt.close(fig)


def analyze(path, wavelength_angstrom, background="als", als_lambda=1e6, als_asymmetry=0.001,
            prominence_fraction=0.03, min_distance_deg=0.15, smoothing_points=7,
            x_column=0, y_column=1, skip_rows=0):
    wavelength = finite(wavelength_angstrom, "wavelength_angstrom", True)
    pattern = read_pattern(path, x_column, y_column, skip_rows)
    base, corrected = preprocess(pattern, background, als_lambda, als_asymmetry)
    peaks = detect(pattern.x, corrected, wavelength, prominence_fraction, min_distance_deg, smoothing_points)
    run = new_run("analyze")
    np.savetxt(run / "processed.csv", np.column_stack([pattern.x, pattern.y, base, corrected]), delimiter=",",
               header="two_theta_deg,raw_intensity,background,corrected_intensity", comments="")
    write_csv(run / "peaks.csv", peaks)
    plot_patterns([(pattern.x, pattern.y, "Raw"), (pattern.x, base, "Background"),
                   (pattern.x, corrected, "Corrected")], run / "pattern")
    result = {"engine": "jade-mcp independent scipy processing (not JADE calculation)", "input": pattern.metadata,
              "parameters": {"wavelength_angstrom": wavelength, "background": background, "als_lambda": als_lambda,
                             "als_asymmetry": als_asymmetry, "prominence_fraction": prominence_fraction,
                             "min_distance_deg": min_distance_deg, "smoothing_points": smoothing_points},
              "peak_count": len(peaks), "peaks": peaks, "output_dir": str(run),
              "limitations": ["Peak detection is not phase identification or quantitative phase analysis.",
                              "Detection widths are approximate; fit peaks and correct instrumental broadening before size/strain.",
                              "No Kalpha2 removal, instrument correction, or Rietveld refinement was applied."]}
    save_json(run / "analysis.json", result)
    rows = "".join(f"<tr><td>{p['two_theta_deg']:.5f}</td><td>{p['d_angstrom']:.5f}</td><td>{p['apparent_fwhm_deg']:.5f}</td></tr>" for p in peaks)
    (run / "report.html").write_text("<!doctype html><meta charset='utf-8'><title>XRD analysis</title>"
        "<style>body{font:16px sans-serif;max-width:960px;margin:40px auto}td,th{padding:8px;border-bottom:1px solid #ddd}img{width:100%}pre{white-space:pre-wrap}</style>"
        f"<h1>XRD 数据处理报告</h1><p>输入：{html.escape(str(pattern.path))}</p><p>独立计算，非 JADE 内部结果。寻峰不等于物相确认。</p>"
        f"<img src='pattern.png'><table><tr><th>2θ / °</th><th>d / Å</th><th>表观半高宽 / °</th></tr>{rows}</table>"
        f"<h2>参数及限制</h2><pre>{html.escape(json.dumps(clean_json(result),ensure_ascii=False,indent=2))}</pre>", encoding="utf-8")
    return result


def pseudo_voigt(x, height, center, fwhm, eta):
    arg = (x - center) / fwhm
    return height * (eta / (1 + 4 * arg**2) + (1 - eta) * np.exp(-4 * np.log(2) * arg**2))


def fit_peaks(path, centers_deg, half_window_deg=0.8, wavelength_angstrom=1.5406):
    p = read_pattern(path)
    centers = np.asarray(centers_deg, dtype=float)
    half = finite(half_window_deg, "half_window_deg", True)
    finite(wavelength_angstrom, "wavelength_angstrom", True)
    if not 1 <= len(centers) <= 20 or not np.all(np.isfinite(centers)) or len(np.unique(centers)) != len(centers):
        raise ValueError("Need 1..20 distinct finite peak centers")
    centers.sort()
    if centers[0] - half < p.x[0] or centers[-1] + half > p.x[-1]:
        raise ValueError("Fit window extends beyond measured data")
    mask = (p.x >= centers[0] - half) & (p.x <= centers[-1] + half)
    x, y = p.x[mask], p.y[mask]
    if len(x) <= 4 * len(centers) + 4:
        raise ValueError("Too few points for requested peak count")
    x0 = float(np.mean(x))
    def model(t, *args):
        result = args[0] + args[1] * (t - x0)
        for j in range(len(centers)):
            result = result + pseudo_voigt(t, *args[2 + 4*j:6 + 4*j])
        return result
    initial, lower, upper = [float(np.percentile(y, 10)), 0.0], [-np.inf, -np.inf], [np.inf, np.inf]
    step = float(np.median(np.diff(x)))
    for j, center in enumerate(centers):
        left = max(center-half/2, (centers[j-1]+center)/2 if j else x[0])
        right = min(center+half/2, (centers[j+1]+center)/2 if j+1<len(centers) else x[-1])
        initial.extend([max(float(np.interp(center, x, y) - initial[0]), 1e-6), center, max(2*step, min(0.15,half/3)), 0.5])
        lower.extend([0, left, step/4, 0])
        upper.extend([np.inf, right, 2*half, 1])
    optimal, covariance = curve_fit(model, x, y, p0=initial, bounds=(lower, upper), maxfev=50000)
    errors = np.sqrt(np.maximum(np.diag(covariance), 0))
    fitted = model(x, *optimal)
    fitted_peaks, warnings = [], []
    for j in range(len(centers)):
        k = 2+4*j
        h, c, w, eta = optimal[k:k+4]
        g, l = np.sqrt(np.pi/(4*np.log(2))), np.pi/2
        area = h*w*((1-eta)*g+eta*l)
        grad = np.array([w*((1-eta)*g+eta*l), 0, h*((1-eta)*g+eta*l), h*w*(l-g)])
        area_var = grad @ covariance[k:k+4,k:k+4] @ grad
        fitted_peaks.append({"center_deg":float(c), "center_esd_deg":float(errors[k+1]),
            "d_angstrom":float(bragg([c],wavelength_angstrom)[0]), "fwhm_deg":float(w), "fwhm_esd_deg":float(errors[k+2]),
            "height":float(h), "height_esd":float(errors[k]), "lorentzian_fraction":float(eta),
            "area":float(area), "area_esd":float(np.sqrt(max(area_var,0)))})
        if any(np.isclose(optimal[q], lower[q],atol=1e-5) or np.isclose(optimal[q], upper[q],atol=1e-5) for q in range(k,k+4)):
            warnings.append(f"Peak {j+1}: parameter at bound; inspect the fit")
    if not np.all(np.isfinite(covariance)) or np.linalg.cond(covariance) > 1e14:
        warnings.append("Covariance is ill-conditioned; parameter uncertainties may be unreliable")
    run = new_run("fit")
    np.savetxt(run/"fit.csv", np.column_stack([x,y,fitted,y-fitted]),delimiter=",",header="two_theta_deg,observed,fit,residual",comments="")
    write_csv(run/"fitted_peaks.csv",fitted_peaks)
    plot_patterns([(x,y,"Observed"),(x,fitted,"Pseudo-Voigt fit"),(x,y-fitted,"Residual")],run/"fit")
    result = {"engine":"scipy simultaneous pseudo-Voigt + linear baseline", "input":p.metadata,
              "parameters":{"centers_deg":centers.tolist(),"half_window_deg":half,"wavelength_angstrom":wavelength_angstrom},
              "peaks":fitted_peaks,"covariance":covariance.tolist(),"rmse":float(np.sqrt(np.mean((y-fitted)**2))),
              "uncertainty":"Unweighted least squares covariance, residual-variance scaled; not full systematic uncertainty.",
              "warnings":warnings,"output_dir":str(run)}
    save_json(run/"fit.json",result)
    return clean_json(result)


def corrected_width(fwhm_deg, instrument_fwhm_deg, profile):
    observed = finite(fwhm_deg,"fwhm_deg",True)
    instrument = finite(instrument_fwhm_deg,"instrument_fwhm_deg")
    if instrument < 0 or observed <= instrument:
        raise ValueError("Require observed FWHM > instrumental FWHM >= 0")
    if profile == "gaussian":
        return math.sqrt(observed**2-instrument**2)
    if profile == "lorentzian":
        return observed-instrument
    raise ValueError("profile must be gaussian or lorentzian; a mixed pseudo-Voigt needs a dedicated instrument model")


def scherrer(two_theta_deg, fwhm_deg, wavelength_angstrom, instrument_fwhm_deg=0, shape_factor=0.9, profile="gaussian"):
    bragg([two_theta_deg],wavelength_angstrom)
    width = corrected_width(fwhm_deg,instrument_fwhm_deg,profile)
    k=finite(shape_factor,"shape_factor",True)
    size = k*wavelength_angstrom/(math.radians(width)*math.cos(math.radians(two_theta_deg/2)))/10
    return {"coherent_domain_size_nm":size,"corrected_fwhm_deg":width,"profile_assumption":profile,
            "parameters":{"two_theta_deg":two_theta_deg,"observed_fwhm_deg":fwhm_deg,
                          "wavelength_angstrom":wavelength_angstrom,"instrument_fwhm_deg":instrument_fwhm_deg,"shape_factor":k},
            "limitations":["Coherent diffraction domain size, not necessarily particle size.",
                           "Strain/defects/overlap can also broaden peaks; use an instrumental standard.",
                           "No uncertainty estimate without peak-width and instrument uncertainties."]}


def williamson_hall(two_theta_deg, fwhm_deg, wavelength_angstrom, instrument_fwhm_deg=None, shape_factor=0.9, profile="gaussian"):
    angles=np.asarray(two_theta_deg,dtype=float)
    widths=np.asarray(fwhm_deg,dtype=float)
    if angles.ndim!=1 or len(angles)<3 or widths.shape!=angles.shape or len(np.unique(angles))<3:
        raise ValueError("Need at least 3 distinct reflections from the same phase and matching widths")
    bragg(angles,wavelength_angstrom)
    finite(shape_factor,"shape_factor",True)
    instruments=np.zeros_like(widths) if instrument_fwhm_deg is None else np.asarray(instrument_fwhm_deg,dtype=float)
    if instruments.shape!=widths.shape:
        raise ValueError("Instrument width array must match reflections")
    beta=np.deg2rad([corrected_width(w,b,profile) for w,b in zip(widths,instruments)])
    theta=np.deg2rad(angles/2)
    x,y=4*np.sin(theta),beta*np.cos(theta)
    coefficients,covariance=np.polyfit(x,y,1,cov=True)
    slope,intercept=map(float,coefficients)
    predicted=slope*x+intercept
    sst=float(np.sum((y-y.mean())**2))
    size=shape_factor*wavelength_angstrom/intercept/10 if intercept>0 else None
    warnings=[]
    if intercept<=0: warnings.append("Nonpositive intercept: physical domain size cannot be extracted")
    if slope<0: warnings.append("Negative slope: do not interpret as physical negative microstrain; inspect model/peaks")
    if instrument_fwhm_deg is None: warnings.append("Instrument broadening has not been subtracted")
    run=new_run("williamson_hall")
    plot_patterns([(x,y,"Reflections"),(x,predicted,"Linear fit")],run/"williamson_hall", "4 sin(theta)","beta cos(theta) (rad)")
    result={"method":"Uniform deformation Williamson-Hall; FWHM convention", "domain_size_nm":size,
            "microstrain":slope if slope>=0 else None,"fitted_slope":slope,"intercept_rad":intercept,
            "slope_esd":float(np.sqrt(covariance[0,0])),"intercept_esd_rad":float(np.sqrt(covariance[1,1])),
            "r_squared":1-float(np.sum((y-predicted)**2))/sst if sst>0 else None,"warnings":warnings,
            "parameters":{"two_theta_deg":angles.tolist(),"fwhm_deg":widths.tolist(),"instrument_fwhm_deg":instruments.tolist(),
                          "wavelength_angstrom":wavelength_angstrom,"shape_factor":shape_factor,"profile":profile},
            "assumptions":"Same phase, correctly indexed isolated peaks, isotropic size/strain; no anisotropy/texture model.","output_dir":str(run)}
    save_json(run/"result.json",result)
    return result


def refine_lattice(two_theta_deg, hkls, wavelength_angstrom, system="cubic"):
    angles=np.asarray(two_theta_deg,dtype=float)
    miller=np.asarray(hkls,dtype=float)
    if miller.shape!=(len(angles),3) or not np.all(np.isfinite(miller)) or np.any(miller!=np.round(miller)) or np.any(np.all(miller==0,axis=1)):
        raise ValueError("hkls must contain one nonzero integer [h,k,l] per reflection")
    d=bragg(angles,wavelength_angstrom)
    h,k,l=miller.T
    if system=="cubic": design=(h*h+k*k+l*l)[:,None]
    elif system=="tetragonal": design=np.column_stack([h*h+k*k,l*l])
    elif system=="hexagonal": design=np.column_stack([4/3*(h*h+h*k+k*k),l*l])
    elif system=="orthorhombic": design=np.column_stack([h*h,k*k,l*l])
    else: raise ValueError("system: cubic, tetragonal, hexagonal, orthorhombic")
    if len(angles)<=design.shape[1] or np.linalg.matrix_rank(design)<design.shape[1]:
        raise ValueError("Need independent indexed reflections, with more reflections than lattice parameters")
    target=1/d**2
    coeff,*_=np.linalg.lstsq(design,target,rcond=None)
    if np.any(coeff<=0): raise ValueError("Nonphysical lattice solution; inspect indexing")
    residual=target-design@coeff
    cov=np.linalg.inv(design.T@design)*(residual@residual)/(len(d)-len(coeff))
    cell=coeff**-0.5
    esd=0.5*coeff**-1.5*np.sqrt(np.maximum(np.diag(cov),0))
    names=["a"] if system=="cubic" else (["a","b","c"] if system=="orthorhombic" else ["a","c"])
    return {"system":system,"cell_angstrom":dict(zip(names,cell.tolist())),"formal_esd_angstrom":dict(zip(names,esd.tolist())),
            "parameters":{"two_theta_deg":angles.tolist(),"hkls":miller.astype(int).tolist(),"wavelength_angstrom":wavelength_angstrom},
            "method":"Unweighted linear least squares in 1/d^2; no zero-shift refinement",
            "limitations":"Formal residual-based errors exclude wavelength, zero-shift, instrument and indexing uncertainties."}


def simulate_cif(cif_path,wavelength_angstrom,two_theta_min=5,two_theta_max=90):
    from pymatgen.core import Structure
    from pymatgen.analysis.diffraction.xrd import XRDCalculator
    source=Path(cif_path).expanduser().resolve(strict=True)
    if source.suffix.lower()!='.cif' or source.stat().st_size>10_000_000: raise ValueError("Need a CIF file <= 10 MB")
    finite(wavelength_angstrom,"wavelength_angstrom",True)
    if not 0<two_theta_min<two_theta_max<180: raise ValueError("Require 0 < min < max < 180")
    structure=Structure.from_file(str(source))
    pattern=XRDCalculator(wavelength=wavelength_angstrom).get_pattern(structure,two_theta_range=(two_theta_min,two_theta_max))
    peaks=[{"two_theta_deg":float(x),"relative_intensity":float(y),"d_angstrom":float(d),"hkls":clean_json(hkl)}
           for x,y,d,hkl in zip(pattern.x,pattern.y,pattern.d_hkls,pattern.hkls)]
    run=new_run("cif")
    write_csv(run/"reference.csv",[{k:v for k,v in row.items() if k!='hkls'} for row in peaks])
    result={"source":str(source),"source_sha256":digest(source),"formula":structure.composition.reduced_formula,
            "wavelength_angstrom":wavelength_angstrom,"two_theta_range":[two_theta_min,two_theta_max],"peaks":peaks,
            "output_dir":str(run),"method":"pymatgen kinematic powder XRD; no texture/instrument/size model"}
    save_json(run/"reference.json",result)
    return result


def match_cifs(path,cif_paths,wavelength_angstrom,tolerance_deg=0.2):
    from scipy.optimize import linear_sum_assignment
    finite(tolerance_deg,"tolerance_deg",True)
    if not 1<=len(cif_paths)<=30: raise ValueError("Provide 1..30 candidate CIFs")
    p=read_pattern(path)
    _,corrected=preprocess(p)
    observed=detect(p.x,corrected,wavelength_angstrom)
    if not observed: raise ValueError("No observed peaks detected")
    ox=np.array([peak['two_theta_deg'] for peak in observed])
    weights=np.array([max(peak['relative_intensity_percent'],0) for peak in observed])
    candidates=[]
    for source in cif_paths:
        ref=simulate_cif(source,wavelength_angstrom,float(p.x[0]),float(p.x[-1]))
        lines=[peak for peak in ref['peaks'] if peak['relative_intensity']>=5]
        if not lines:
            candidates.append({"formula":ref['formula'],"cif":ref['source'],"score":0,"matches":[],"note":"No reference peaks >= 5% in range"})
            continue
        rx=np.array([peak['two_theta_deg'] for peak in lines])
        cost=abs(ox[:,None]-rx[None,:])
        # Dummy columns permit unmatched observations; one-to-one assignment prevents reusing a reference line.
        extended=np.column_stack([np.where(cost<=tolerance_deg,cost,1e6),np.full((len(ox),len(ox)),tolerance_deg+1e-8)])
        obs_idx,ref_idx=linear_sum_assignment(extended)
        valid=[(i,j) for i,j in zip(obs_idx,ref_idx) if j<len(rx) and cost[i,j]<=tolerance_deg]
        matched_observed=float(sum(weights[i] for i,j in valid)/weights.sum())
        total_ref=sum(peak['relative_intensity'] for peak in lines)
        matched_reference=sum(lines[j]['relative_intensity'] for i,j in valid)/total_ref
        score=2*matched_observed*matched_reference/(matched_observed+matched_reference) if matched_observed+matched_reference else 0
        candidates.append({"formula":ref['formula'],"cif":ref['source'],"source_sha256":ref['source_sha256'],
            "score":score,"observed_intensity_coverage":matched_observed,"reference_intensity_coverage":matched_reference,
            "matches":[{"observed_deg":float(ox[i]),"reference_deg":float(rx[j]),"delta_deg":float(ox[i]-rx[j])} for i,j in valid]})
    candidates.sort(key=lambda row:row['score'],reverse=True)
    run=new_run("match")
    result={"input":p.metadata,"wavelength_angstrom":wavelength_angstrom,"tolerance_deg":tolerance_deg,"candidates":candidates,
            "limitations":["Restricted to supplied CIFs, not a database-wide search.","Heuristic position/coverage score, not a calibrated probability or phase fraction.",
                           "Mixtures, preferred orientation, solid solutions and peak shifts require expert review."],"output_dir":str(run)}
    save_json(run/"matches.json",result)
    return result


def compare_patterns(paths,normalize=True,offset=110):
    if not 1<=len(paths)<=30: raise ValueError("Need 1..30 input patterns")
    offset=finite(offset,"offset")
    curves,inputs=[],[]
    for j,path in enumerate(paths):
        p=read_pattern(path)
        y=p.y.copy()
        if normalize:
            span=np.ptp(y)
            y=100*(y-y.min())/span if span else np.zeros_like(y)
        curves.append((p.x,y+j*offset,p.path.stem))
        inputs.append(p.metadata)
    run=new_run("compare")
    plot_patterns(curves,run/"comparison",ylabel="Normalized intensity + offset" if normalize else "Source intensity + offset")
    result={"inputs":inputs,"normalize":normalize,"offset":offset,"output_dir":str(run),
            "notes":"Visual comparison on original angle grids; does not align, resample, or quantify phase changes."}
    save_json(run/"comparison.json",result)
    return result
