#!/usr/bin/env python3
"""Put every modality of one session on the master timeline, binned to a
common grid, so a human can see whether they are all on time at the same
time — and measure each stream's arrival delta, jitter and drift.

master_t (s) = (ros_timestamp_ns - master_start_ns) / 1e9
media_t  (s) = master_t - clock.ros_start_offset_s        (video/audio local)
"""
from __future__ import annotations

import csv
import json
import os
import sys
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

# The corpus (data/, data_synced/, features/) lives in the ARC-CognitiveLoad repo.
# Point ARC_REPO at it; the default assumes it is checked out next to this repo.
REPO = Path(os.environ.get("ARC_REPO", Path(__file__).resolve().parent.parent / "ARC-CognitiveLoad"))
SYNCED = REPO / "data_synced"
RAW = REPO / "data"
FEATURES = REPO / "features"
STORE = Path(__file__).resolve().parent / "store"
GRID_HZ = 10.0


def bin_to_grid(t: np.ndarray, v: np.ndarray, n_bins: int) -> list:
    """Mean of v per grid bin on [0, n_bins/GRID_HZ); NaN where no samples."""
    t = np.asarray(t, float); v = np.asarray(v, float)
    ok = np.isfinite(t) & np.isfinite(v) & (t >= 0)
    t, v = t[ok], v[ok]
    idx = np.floor(t * GRID_HZ).astype(int)
    keep = idx < n_bins
    idx, v = idx[keep], v[keep]
    s = np.bincount(idx, weights=v, minlength=n_bins)
    c = np.bincount(idx, minlength=n_bins)
    out = np.full(n_bins, np.nan)
    nz = c > 0
    out[nz] = s[nz] / c[nz]
    return [None if not np.isfinite(x) else round(float(x), 4) for x in out]


def jitter_ms(ts_ns: np.ndarray) -> dict:
    d = np.diff(ts_ns.astype(np.int64)) / 1e6
    d = d[d > 0]
    if len(d) == 0:
        return {}
    return {"median_dt_ms": round(float(np.median(d)), 2),
            "p99_dt_ms": round(float(np.percentile(d, 99)), 1),
            "max_dt_ms": round(float(d.max()), 1)}


def audio_lane(path: Path, clock_off: float, n_bins: int, label: str, delta: float) -> dict:
    with wave.open(str(path), "rb") as w:
        sr, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        raw = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32)
    if ch > 1:
        raw = raw.reshape(-1, ch).mean(axis=1)
    hop = int(sr / GRID_HZ)
    m = len(raw) // hop
    rms = np.sqrt((raw[: m * hop].reshape(m, hop) ** 2).mean(axis=1))
    t = np.arange(m) / GRID_HZ + clock_off            # wav t=0 is clock start
    return {"id": path.stem, "group": "audio", "label": label, "unit": "rms",
            "values": bin_to_grid(t, np.log1p(rms), n_bins),
            "arrival_delta_s": delta, "default_offset_s": 0.0,
            "timebase": "clock (ROS-resampled)", "sr_hz": sr}


def camera_lanes(sid: str, participant: str, cam: str, clock_off: float, n_bins: int,
                 delta: float, fine: dict | None) -> list:
    p = FEATURES / f"video/mediapipe/{participant}/{sid}/video/{cam}/{cam}_mediapipe_motion.csv"
    if not p.exists():
        return []
    rows = list(csv.DictReader(open(p)))
    f = lambda k: np.array([float(r[k]) if r.get(k) not in (None, "") else np.nan for r in rows])
    t = f("time_s") + clock_off
    short = cam.replace("cameras_", "").replace("_image_raw", "")
    fine_off = fine["offset_s"] if fine and fine.get("trusted") else 0.0
    note = (f"fine visual offset {fine['offset_s']:+.3f}s ({fine['status']})" if fine
            else "no fine-sync estimate")
    lanes = [{"id": f"{cam}__motion", "group": "video", "label": f"{short} · face motion",
              "values": bin_to_grid(t, f("motion_signal"), n_bins),
              "arrival_delta_s": delta, "default_offset_s": fine_off,
              "fine_status": fine.get("status") if fine else None, "note": note,
              "timebase": "clock (ROS-resampled)"}]
    blink = f("blendshape_eyeBlinkLeft") + f("blendshape_eyeBlinkRight")
    if np.nanstd(blink) > 1e-4:
        lanes.append({"id": f"{cam}__blink", "group": "video", "label": f"{short} · blink",
                      "values": bin_to_grid(t, blink, n_bins),
                      "arrival_delta_s": delta, "default_offset_s": fine_off,
                      "fine_status": fine.get("status") if fine else None, "note": note,
                      "timebase": "clock (ROS-resampled)"})
    return lanes


def fnirs_lanes(csvdir: Path, master_ns: int, n_bins: int, delta: float) -> tuple[list, dict]:
    """De-jitter chunked LSL samples with their own LSL clock, and measure how
    that clock drifts against the ROS host clock."""
    ts_path, s_path = csvdir / "lsl_OxySoft_timestamps.csv", csvdir / "lsl_OxySoft_samples.csv"
    if not (ts_path.exists() and s_path.exists()):
        return [], {}
    ros_chunk, lsl_flat, lsl_last = [], [], []
    with open(ts_path) as fh:
        r = csv.reader(fh); next(r)
        for row in r:
            vals = [float(x) for x in row[1:-1] if x != ""]
            if not vals:
                continue
            ros_chunk.append(int(row[0])); lsl_flat.extend(vals); lsl_last.append(vals[-1])
    ros_chunk = np.array(ros_chunk, dtype=np.int64); lsl_last = np.array(lsl_last)
    # ROS receipt time of a chunk ≈ its last sample's LSL time + constant lag
    rel = (ros_chunk - master_ns) / 1e9
    b, a = np.polyfit(lsl_last, rel, 1)                # rel ≈ a + b*lsl
    resid = rel - (a + b * lsl_last)
    drift_ppm = (b - 1.0) * 1e6
    samples, ros_rows = [], []
    with open(s_path) as fh:
        r = csv.reader(fh); hdr = next(r)
        nch = len([h for h in hdr if h.startswith("ch_")])
        for row in r:
            ros_rows.append(int(row[0]))
            samples.append([float(x) if x else np.nan for x in row[1:1 + nch]])
    S = np.array(samples)
    n = min(len(S), len(lsl_flat))
    t_lsl = a + b * np.array(lsl_flat[:n])              # de-jittered master time
    t_ros = (np.array(ros_rows[:n], dtype=np.int64) - master_ns) / 1e9   # chunk arrival
    # Even channels are O2Hb (see memory: interleaved). Show the O2Hb mean and
    # one raw channel so a hemodynamic event is visible.
    o2 = np.nanmean(S[:n, 0::2], axis=1)
    meta = {"lsl_vs_ros_drift_ppm": round(float(drift_ppm), 1),
            "lsl_fit_lag_s": round(float(a), 3),
            "chunk_resid_p99_ms": round(float(np.percentile(np.abs(resid), 99) * 1e3), 1),
            "n_chunks": int(len(ros_chunk)), "n_samples": int(n),
            "chunk_jitter": jitter_ms(np.unique(ros_chunk))}
    common = {"group": "fnirs", "arrival_delta_s": delta, "default_offset_s": 0.0}
    return ([{"id": "fnirs__o2hb_mean_dejittered", "label": "fNIRS · O2Hb mean (LSL-dejittered)",
              "values": bin_to_grid(t_lsl, o2, n_bins), "timebase": "LSL clock → ROS fit", **common},
             {"id": "fnirs__o2hb_mean_arrival", "label": "fNIRS · O2Hb mean (chunk arrival time)",
              "values": bin_to_grid(t_ros, o2, n_bins), "timebase": "ROS chunk receipt", **common}],
            meta)


def thought_lanes(csvdir: Path, master_ns: int, n_bins: int, deltas: dict) -> tuple[list, dict]:
    lanes, meta = [], {}
    for k in range(0, 5):
        p = csvdir / f"thought_ch{k}.csv"
        if not p.exists():
            continue
        rows = list(csv.DictReader(open(p)))
        ts = np.array([int(r["timestamp"]) for r in rows], dtype=np.int64)
        v = np.array([float(r["ch_0"]) if r["ch_0"] else np.nan for r in rows])
        t = (ts - master_ns) / 1e9
        meta[f"ch{k}"] = jitter_ms(ts)
        lanes.append({"id": f"thought__ch{k}", "group": "biosensor", "label": f"thought ch{k}",
                      "values": bin_to_grid(t, v, n_bins), "timebase": "ROS per-row",
                      "arrival_delta_s": deltas.get(f"thought_ch{k}.csv", 0.0),
                      "default_offset_s": 0.0, "jitter": meta[f"ch{k}"]})
    return lanes, meta


def tobii_lanes(sid: str, participant: str, master_ns: int, n_bins: int) -> tuple[list, dict]:
    cands = list((RAW / participant / sid / "tobii").glob("*.tsv")) if (RAW / participant / sid / "tobii").exists() else []
    if not cands:
        return [], {"status": "no tsv"}
    p = cands[0]
    with open(p, newline="", encoding="utf-8-sig") as fh:
        r = csv.DictReader(fh, delimiter="\t"); rows = list(r); cols = r.fieldnames
    def col(*names):
        for n in names:
            for c in cols:
                if c.strip().lower() == n.lower():
                    return c
        return None
    c_rec = col("Recording timestamp [μs]", "Recording timestamp [us]", "Recording timestamp")
    c_date = col("Recording date UTC", "Recording date")
    c_start = col("Recording start time UTC", "Recording start time")
    c_pupil = col("Pupil diameter filtered [mm]", "Pupil diameter left [mm]")
    c_gx = col("Gaze point X [DACS px]", "Gaze point X")
    if not (c_rec and c_date and c_start):
        return [], {"status": "columns not found", "cols": cols[:20]}
    first = next((x for x in rows if x.get(c_date) and x.get(c_start)), None)
    if not first:
        return [], {"status": "no start row"}
    start = None
    for fmt in ("%m/%d/%Y %H:%M:%S.%f", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%d/%m/%Y %H:%M:%S.%f"):
        try:
            start = datetime.strptime(f"{first[c_date]} {first[c_start]}", fmt).replace(tzinfo=timezone.utc); break
        except ValueError:
            continue
    if start is None:
        return [], {"status": f"unparsed start {first[c_date]} {first[c_start]}"}
    start_epoch = start.timestamp()
    offset = start_epoch - master_ns / 1e9          # tobii_start_utc − ros_start_utc
    rec = np.array([float(x[c_rec]) if x.get(c_rec) else np.nan for x in rows]) / 1e6
    t = rec + offset
    lanes = []
    for cid, cc, lab in (("tobii__pupil", c_pupil, "Tobii · pupil diameter"), ("tobii__gaze_x", c_gx, "Tobii · gaze x")):
        if not cc:
            continue
        v = np.array([float(x[cc]) if x.get(cc) not in (None, "") else np.nan for x in rows])
        lanes.append({"id": cid, "group": "gaze", "label": lab, "values": bin_to_grid(t, v, n_bins),
                      "timebase": "UTC wall-clock delta (unverified)", "arrival_delta_s": round(offset, 3),
                      "default_offset_s": 0.0, "note": "sync = tobii_start_utc − ros_start_utc; no content check"})
    meta = {"status": "ok", "utc_offset_s": round(offset, 3), "start_utc": start.isoformat(),
            "n_rows": len(rows), "span_s": round(float(np.nanmax(rec)), 1)}
    return lanes, meta


def build(sid: str) -> dict:
    idx = json.loads((STORE / "index.json").read_text())
    s = next(x for x in idx["sessions"] if x["session_id"] == sid)
    sdir = SYNCED / s["participant"] / s["dir_name"]
    man = json.loads((sdir / "sync_manifest.json").read_text())
    master_ns = s["master_start_ns"]; clock_off = s["ros_start_offset_s"]
    n_bins = int(np.ceil((s["duration_s"] + clock_off) * GRID_HZ))
    deltas = {}
    for st in man["streams"]:
        nm = (st.get("source") or "").split("/")[-1]
        deltas[nm] = (st.get("timing") or {}).get("ros_start_offset_s")
    fine = (s.get("sync") or {}).get("cameras", {})
    lanes, meta = [], {"master_start_ns": master_ns, "clock_ros_start_offset_s": clock_off,
                       "grid_hz": GRID_HZ, "arrival_deltas_s": deltas}
    for cam in s["cameras"]:
        lanes += camera_lanes(sid, s["participant"], cam, clock_off, n_bins, deltas.get(cam + ".mp4"), fine.get(cam))
    for mic, lab in (("audio_SteelSeries_Alias_USB_Audio_audio_raw", "SteelSeries mic · loudness"),
                     ("audio_Logitech_BRIO_USB_Audio_audio_raw", "Logitech mic · loudness")):
        p = sdir / "audio" / f"{mic}.wav"
        if p.exists():
            lanes.append(audio_lane(p, clock_off, n_bins, lab, deltas.get(mic + ".wav")))
    fl, fm = fnirs_lanes(sdir / "csv", master_ns, n_bins, deltas.get("lsl_OxySoft_samples.csv"))
    lanes += fl; meta["fnirs"] = fm
    tl, tm = thought_lanes(sdir / "csv", master_ns, n_bins, deltas)
    lanes += tl; meta["thought"] = tm
    gl, gm = tobii_lanes(sid, s["participant"], master_ns, n_bins)
    lanes += gl; meta["tobii"] = gm
    return {"session_id": sid, "n_bins": n_bins, "grid_hz": GRID_HZ, "duration_s": s["duration_s"],
            "clock_ros_start_offset_s": clock_off, "reference_camera": s["cameras"][0] if s["cameras"] else None,
            "lanes": lanes, "meta": meta}


if __name__ == "__main__":
    sid = sys.argv[1]
    out = STORE / "signals"; out.mkdir(parents=True, exist_ok=True)
    d = build(sid)
    (out / f"{sid}.json").write_text(json.dumps(d))
    print(f"{sid}: {len(d['lanes'])} lanes, {d['n_bins']} bins @ {GRID_HZ} Hz")
    print(" fnirs :", json.dumps(d["meta"]["fnirs"]))
    print(" tobii :", json.dumps(d["meta"]["tobii"]))
    print(" thought:", json.dumps(d["meta"]["thought"]))
    print(" lanes :", [l["id"] for l in d["lanes"]])
