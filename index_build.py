#!/usr/bin/env python3
"""Build a single canonical index of the ARC-CognitiveLoad corpus.

Everything the annotation platform shows is derived from this index, and every
time value in it is absolute nanoseconds on one declared master timeline.

Master timeline  = ROS bag global start for the session
                 = clock.start_ns - clock.ros_start_offset_s * 1e9
Media t=0        = clock.start_ns  (first frame of the clock video)
=> media_time_s  = master_time_s - ros_start_offset_s
"""
from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

# The corpus (data/, data_synced/, features/) lives in the ARC-CognitiveLoad repo.
# Point ARC_REPO at it; the default assumes it is checked out next to this repo.
REPO = Path(os.environ.get("ARC_REPO", Path(__file__).resolve().parent.parent / "ARC-CognitiveLoad"))
SYNCED = REPO / "data_synced"
RAW = REPO / "data"
FEATURES = REPO / "features"

# Streams we know the rig can produce, in display order.
CAMERAS = [
    "cameras_d435_rgb_image_raw",
    "cameras_d435_ir_image_raw",
    "cameras_brio0_rgb_image_raw",
    "cameras_brio1_rgb_image_raw",
    "cameras_brio2_ir_image_raw",
    "cameras_brio3_ir_image_raw",
    "cameras_seek_thermal_image_raw",
    "cameras_video4_ir_image_raw",
    "cameras_video18_depth_image_raw",
]
MICS = [
    "audio_SteelSeries_Alias_USB_Audio_audio_raw",
    "audio_Logitech_BRIO_USB_Audio_audio_raw",
]
# Streams absent *by design* on the Feb 2026 pilot rig (P01). Marking these as
# expected-absent is what stops P01 reading as permanently broken.
PILOT_ABSENT = {
    "cameras_d435_rgb_image_raw",
    "cameras_d435_ir_image_raw",
    "cameras_brio0_rgb_image_raw",
    "cameras_video18_depth_image_raw",
}
MAIN_ABSENT = {"cameras_video4_ir_image_raw"}


def canon_session(sid: str) -> str:
    """P10_S01_02 and p10_S01_02 are the same session; the corpus has both."""
    m = re.match(r"^([Pp])(\d+)_(.+)$", sid)
    return f"P{int(m.group(2)):02d}_{m.group(3)}" if m else sid.upper()


def load_annotations() -> dict:
    """Prefer the 38-session Drive export over the stale 26-session file."""
    candidates = [
        RAW / "annotations_timestamp_14FromGoogleDrive.csv",
        REPO / "3_train_and_evaluate_cogloadestimation-3.0HighLow/inputs/annotations_timestamp_14participants.csv",
        RAW / "annotations_timestamp.csv",
    ]
    src = next((p for p in candidates if p.exists()), None)
    out: dict[str, list] = {}
    if not src:
        return out
    with open(src, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            if not r.get("session_id") or not r.get("start_s"):
                continue
            try:
                start_s, end_s = float(r["start_s"]), float(r["end_s"])
            except (TypeError, ValueError):
                continue
            out.setdefault(canon_session(r["session_id"]), []).append(
                {
                    "segment": r["segment"],
                    "condition": r.get("condition", ""),
                    "cognitive_load": r.get("cognitive_load", ""),
                    "question_present": r.get("question_present", ""),
                    # as recorded: seconds from ROS bag global start
                    "start_master_s": start_s,
                    "end_master_s": end_s,
                    "start_mm_ss": r.get("start_mm_ss", ""),
                    "end_mm_ss": r.get("end_mm_ss", ""),
                    "start_timestamp_ns": r.get("start_timestamp_ns", ""),
                    "source": src.name,
                    "precision_s": 1.0,   # boundaries were read off mm:ss
                    "anchor": "ros_global_start",
                }
            )
    for rows in out.values():
        rows.sort(key=lambda x: x["start_master_s"])
    return out


def load_sync_offsets() -> dict:
    """Fine per-camera visual offsets, where the MediaPipe estimator has run."""
    out: dict[str, dict] = {}
    roots = [
        FEATURES / "video/mediapipe_sync",
        FEATURES / "video/mediapipe_sync_graph",
        FEATURES / "video/mediapipe_sync_blink",
    ]
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("video_sync_offsets.csv"):
            try:
                rows = list(csv.DictReader(open(path)))
            except OSError:
                continue
            if not rows:
                continue
            sid = canon_session(rows[0].get("session_id") or path.parent.name)
            bucket = out.setdefault(sid, {"variant": root.name, "cameras": {}})
            for r in rows:
                cam = r.get("camera_id")
                if not cam:
                    continue
                try:
                    off = float(r.get("offset_s_to_add") or 0.0)
                except ValueError:
                    off = 0.0
                status = (r.get("status") or "").strip()
                bucket["cameras"][cam] = {
                    "offset_s": off,
                    "status": status,
                    # Only `ok` / `reference` offsets may be applied. A weak or
                    # anti-correlated estimate must never push a stream further out.
                    "trusted": status in ("ok", "reference"),
                    "score": r.get("best_score", ""),
                    "drift_range_s": r.get("drift_range_s", ""),
                    "drift_warning": r.get("drift_warning", ""),
                    "reference": r.get("reference_camera_id", ""),
                }
    return out


def load_transcript(sid: str, participant: str) -> dict | None:
    for mic in MICS:
        dia = FEATURES / f"audio/diarization/{participant}/{sid}/audio/{mic}"
        wh = FEATURES / f"audio/whisper/{participant}/{sid}/audio/{mic}"
        segs = dia / "transcript_segments_diarized.json"
        roles = dia / "speaker_role_assignment.json"
        plain = wh / "transcript_segments.json"
        payload: dict = {"mic": mic, "segments": [], "roles": {}, "source": None}
        if segs.exists():
            try:
                payload["segments"] = json.load(open(segs))
                payload["source"] = "diarized"
            except (OSError, json.JSONDecodeError):
                pass
        if not payload["segments"] and plain.exists():
            try:
                payload["segments"] = json.load(open(plain))
                payload["source"] = "whisper_only"
            except (OSError, json.JSONDecodeError):
                pass
        if roles.exists():
            try:
                payload["roles"] = json.load(open(roles))
            except (OSError, json.JSONDecodeError):
                pass
        if payload["segments"]:
            return payload
    return None


def build() -> dict:
    annotations = load_annotations()
    offsets = load_sync_offsets()
    sessions = []

    for pdir in sorted(SYNCED.iterdir()):
        if not pdir.is_dir() or not re.match(r"^[Pp]\d+$", pdir.name):
            continue
        participant = f"P{int(pdir.name[1:]):02d}"
        for sdir in sorted(pdir.iterdir()):
            if not sdir.is_dir():
                continue
            sid = canon_session(sdir.name)
            manifest_path = sdir / "sync_manifest.json"
            manifest = {}
            if manifest_path.exists():
                try:
                    manifest = json.load(open(manifest_path))
                except (OSError, json.JSONDecodeError):
                    manifest = {}
            clock = manifest.get("clock", {}) or {}
            ros_off = float(clock.get("ros_start_offset_s") or 0.0)
            duration = float(clock.get("duration_s") or 0.0)

            present_cams, cam_meta = [], {}
            for cam in CAMERAS:
                f = sdir / "video" / f"{cam}.mp4"
                if f.exists() and f.stat().st_size > 0:
                    present_cams.append(cam)
                    cam_meta[cam] = {"bytes": f.stat().st_size}
            rig = "pilot_feb2026" if "cameras_d435_rgb_image_raw" not in present_cams else "main"
            expected_absent = PILOT_ABSENT if rig == "pilot_feb2026" else MAIN_ABSENT

            streams = {}
            for cam in CAMERAS:
                if cam in present_cams:
                    streams[cam] = {"status": "present_ok", "bytes": cam_meta[cam]["bytes"]}
                elif cam in expected_absent:
                    streams[cam] = {"status": "absent_by_design", "note": f"not on rig {rig}"}
                else:
                    streams[cam] = {"status": "absent_unexpected"}

            present_mics = []
            for mic in MICS:
                f = sdir / "audio" / f"{mic}.wav"
                if f.exists() and f.stat().st_size > 0:
                    streams[mic] = {"status": "present_ok", "bytes": f.stat().st_size}
                    present_mics.append(mic)
                else:
                    streams[mic] = {"status": "absent_unexpected"}

            csvd = sdir / "csv"
            fnirs_ok = (csvd / "lsl_OxySoft_samples.csv").exists()
            streams["fnirs_oxysoft"] = {"status": "present_ok" if fnirs_ok else "absent_unexpected"}
            thought = sorted(csvd.glob("thought_ch*.csv")) if csvd.exists() else []
            streams["thought_biosensors"] = {
                "status": "present_ok" if thought else "absent_unexpected",
                "note": f"{len(thought)} channels"
                + (" (ch0 not recorded on pilot rig)" if rig == "pilot_feb2026" else ""),
            }
            tobii_dir = sdir / "tobii"
            tobii_files = list(tobii_dir.glob("*")) if tobii_dir.exists() else []
            streams["tobii_gaze"] = {
                "status": "present_ok" if tobii_files else "absent_unexpected",
                "note": "UTC-offset sync, no content-based verification",
            }

            segs = annotations.get(sid, [])
            # Project label boundaries onto the media timeline and flag the
            # two defects the raw sheet carries: the ~0.8s anchor shift and
            # end times that run past the end of the recording.
            fixed = []
            for s in segs:
                start_media = s["start_master_s"] - ros_off
                end_media = s["end_master_s"] - ros_off
                overrun = max(0.0, end_media - duration) if duration else 0.0
                fixed.append(
                    {
                        **s,
                        "start_media_s": round(start_media, 3),
                        "end_media_s": round(min(end_media, duration) if duration else end_media, 3),
                        "overruns_by_s": round(overrun, 3),
                        "clipped": overrun > 0.05,
                    }
                )

            gaps = []
            if fixed:
                if fixed[0]["start_media_s"] > 1.0:
                    gaps.append({"start_s": 0.0, "end_s": fixed[0]["start_media_s"], "kind": "head"})
                for a, b in zip(fixed, fixed[1:]):
                    g = b["start_media_s"] - a["end_media_s"]
                    if g > 1.0:
                        gaps.append({"start_s": a["end_media_s"], "end_s": b["start_media_s"], "kind": "interior"})
                if duration and duration - fixed[-1]["end_media_s"] > 1.0:
                    gaps.append({"start_s": fixed[-1]["end_media_s"], "end_s": duration, "kind": "tail"})

            transcript = load_transcript(sid, participant)
            sync = offsets.get(sid)

            sessions.append(
                {
                    "session_id": sid,
                    "dir_name": sdir.name,
                    "participant": participant,
                    "rig": rig,
                    "clock_stream": clock.get("stream", ""),
                    "clock_selection_reason": clock.get("selection_reason", ""),
                    "duration_s": duration,
                    "fps": clock.get("fps"),
                    "ros_start_offset_s": ros_off,
                    "master_start_ns": int(clock.get("start_ns", 0) - ros_off * 1e9) if clock.get("start_ns") else None,
                    "media_start_ns": clock.get("start_ns"),
                    "cameras": present_cams,
                    "mics": present_mics,
                    "streams": streams,
                    "segments": fixed,
                    "gaps": gaps,
                    "labeled": bool(fixed),
                    "sync": sync,
                    "fine_sync_available": bool(sync),
                    "transcript_available": bool(transcript),
                    "transcript": transcript,
                    "n_unexpected_absent": sum(
                        1 for v in streams.values() if v["status"] == "absent_unexpected"
                    ),
                }
            )

    # Sessions that exist in raw but never made it through the sync pipeline.
    orphans = []
    if RAW.exists():
        synced_ids = {s["session_id"] for s in sessions}
        for pdir in sorted(RAW.iterdir()):
            if not pdir.is_dir() or not re.match(r"^[Pp]\d+$", pdir.name):
                continue
            for sdir in sorted(pdir.iterdir()):
                if sdir.is_dir() and canon_session(sdir.name) not in synced_ids:
                    orphans.append(canon_session(sdir.name))

    return {
        "sessions": sessions,
        "orphans": orphans,
        "cameras": CAMERAS,
        "mics": MICS,
        "signals": ["fnirs_oxysoft", "thought_biosensors", "tobii_gaze"],
        "annotation_source": next(
            (s["segments"][0]["source"] for s in sessions if s["segments"]), None
        ),
    }


if __name__ == "__main__":
    idx = build()
    out = Path(__file__).resolve().parent / "store" / "index.json"
    out.write_text(json.dumps(idx, indent=1))
    n_lab = sum(1 for s in idx["sessions"] if s["labeled"])
    n_fine = sum(1 for s in idx["sessions"] if s["fine_sync_available"])
    n_tr = sum(1 for s in idx["sessions"] if s["transcript_available"])
    print(f"sessions={len(idx['sessions'])} labeled={n_lab} fine_sync={n_fine} transcript={n_tr}")
    print(f"orphans={idx['orphans']}")
    print(f"wrote {out}")
