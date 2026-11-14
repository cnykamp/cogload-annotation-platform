#!/usr/bin/env python3
"""Run the actual CogLoad pipeline and web UI with entirely synthetic data.

The fake participant, streams, timestamps, transcripts, and signal values are
generated locally. A temporary workspace isolates the demo from real research
data and avoids overwriting an existing store/index.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave
from array import array
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SID = "P99_S01_01"  # Fictional participant/session identifier
PARTICIPANT = "P99"
CAMERA = "cameras_brio1_rgb_image_raw"
MIC = "audio_SteelSeries_Alias_USB_Audio_audio_raw"
DURATION = 12
ROS_OFFSET_S = 0.8
MASTER_NS = 1_700_000_000_000_000_000  # Fixed fictional timestamp
MEDIA_NS = MASTER_NS + int(ROS_OFFSET_S * 1_000_000_000)


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def write_csv(path: Path, fieldnames: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(fieldnames)
        writer.writerows(rows)


def generate_video(path: Path) -> None:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("FFmpeg is required for the synthetic video. macOS: brew install ffmpeg")
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=320x180:rate=12", "-t", str(DURATION),
        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
        "-movflags", "+faststart", str(path),
    ], check=True)


def generate_corpus(corpus: Path) -> None:
    session = corpus / "data_synced" / PARTICIPANT / SID
    csvdir = session / "csv"
    generate_video(session / "video" / f"{CAMERA}.mp4")

    # Artificial tones only: no actual voices, speech, or participant recordings.
    sr = 16_000
    samples = array("h")
    for i in range(DURATION * sr):
        t = i / sr
        amp = 0.18 if 2 <= t < 3 else (0.12 if 7 <= t < 8 else 0.0)
        hz = 440 if t < 4 else 660
        samples.append(int(32767 * amp * math.sin(2 * math.pi * hz * t)))
    audio = session / "audio" / f"{MIC}.wav"
    audio.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(audio), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(samples.tobytes())

    write_json(session / "sync_manifest.json", {
        "clock": {
            "start_ns": MEDIA_NS, "ros_start_offset_s": ROS_OFFSET_S,
            "duration_s": DURATION, "fps": 12, "stream": CAMERA,
            "selection_reason": "entirely synthetic demonstration",
        },
        "streams": [
            {"source": f"video/{CAMERA}.mp4",
             "timing": {"ros_start_offset_s": ROS_OFFSET_S}},
            {"source": f"audio/{MIC}.wav",
             "timing": {"ros_start_offset_s": ROS_OFFSET_S}},
            {"source": "csv/thought_ch0.csv",
             "timing": {"ros_start_offset_s": 0.0}},
            {"source": "csv/lsl_OxySoft_samples.csv",
             "timing": {"ros_start_offset_s": 0.0}},
        ],
    })

    # Recorded annotations are master-relative, not seconds into the video.
    # The actual indexer must subtract the 0.8-second offset.
    write_csv(
        corpus / "data" / "annotations_timestamp.csv",
        ["session_id", "segment", "condition", "cognitive_load",
         "question_present", "start_s", "end_s", "start_mm_ss", "end_mm_ss"],
        [
            [SID, "Synthetic baseline", "A", "Low", "No", 2.8, 5.8, "00:02", "00:05"],
            [SID, "Synthetic challenge", "B", "High", "Yes", 7.8, 11.3, "00:07", "00:10"],
        ],
    )

    write_csv(
        corpus / "features" / "video" / "mediapipe_sync" / PARTICIPANT / SID /
        "video_sync_offsets.csv",
        ["session_id", "camera_id", "offset_s_to_add", "status",
         "best_score", "reference_camera_id", "drift_range_s", "drift_warning"],
        [[SID, CAMERA, 0.0, "reference", 1.0, CAMERA, 0.0, ""]],
    )
    motion = []
    for i in range(DURATION * 10):
        t = i / 10
        burst = 0.7 if abs(t - 2.5) < 0.2 or abs(t - 7.5) < 0.2 else 0.0
        motion.append([
            f"{t:.1f}", f"{0.1 * math.sin(t) + burst:.5f}",
            f"{burst:.3f}", f"{burst:.3f}",
        ])
    write_csv(
        corpus / "features" / "video" / "mediapipe" / PARTICIPANT / SID /
        "video" / CAMERA / f"{CAMERA}_mediapipe_motion.csv",
        ["time_s", "motion_signal", "blendshape_eyeBlinkLeft",
         "blendshape_eyeBlinkRight"],
        motion,
    )

    # Placeholder text tests transcript controls. It is not real ASR output.
    write_json(
        corpus / "features" / "audio" / "whisper" / PARTICIPANT / SID /
        "audio" / MIC / "transcript_segments.json",
        [
            {"start_s": 2.0, "end_s": 3.0,
             "text": "Synthetic placeholder for tone A.", "speaker_label": "SPEAKER_00"},
            {"start_s": 7.0, "end_s": 8.0,
             "text": "Synthetic placeholder for tone B.", "speaker_label": "SPEAKER_00"},
        ],
    )

    write_csv(
        csvdir / "thought_ch0.csv", ["timestamp", "ch_0"],
        [[MASTER_NS + int(i * 100_000_000), f"{math.sin(i / 7):.6f}"]
         for i in range(DURATION * 10)],
    )

    # Five LSL sample times per synthetic fNIRS receipt chunk.
    timestamps, fnirs_samples = [], []
    for chunk in range(24):
        times = [chunk * 0.5 + k * 0.1 for k in range(5)]
        receipt = MASTER_NS + int((times[-1] + 0.025) * 1_000_000_000)
        timestamps.append([
            receipt, *[f"{1000 + t:.6f}" for t in times], "synthetic",
        ])
        for t in times:
            sample_ns = MASTER_NS + int(t * 1_000_000_000)
            fnirs_samples.append([
                sample_ns, f"{0.7 + 0.15 * math.sin(t):.6f}",
                f"{0.3 + 0.1 * math.cos(t):.6f}",
                f"{0.5 + 0.1 * math.sin(t + 1):.6f}",
                f"{0.2 + 0.05 * math.cos(t + 1):.6f}",
            ])
    write_csv(
        csvdir / "lsl_OxySoft_timestamps.csv",
        ["ros_timestamp_ns", "lsl_0", "lsl_1", "lsl_2",
         "lsl_3", "lsl_4", "source"],
        timestamps,
    )
    write_csv(
        csvdir / "lsl_OxySoft_samples.csv",
        ["ros_timestamp_ns", "ch_0", "ch_1", "ch_2", "ch_3"],
        fnirs_samples,
    )

    # Synthetic gaze samples, with a UTC anchor from the fictional epoch.
    first = datetime.fromtimestamp(MASTER_NS / 1e9, tz=timezone.utc)
    date, clock = first.strftime("%m/%d/%Y"), first.strftime("%H:%M:%S.%f")
    gaze = corpus / "data" / PARTICIPANT / SID / "tobii" / "synthetic_gaze.tsv"
    gaze.parent.mkdir(parents=True, exist_ok=True)
    with gaze.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow([
            "Recording date UTC", "Recording start time UTC",
            "Recording timestamp [us]", "Pupil diameter filtered [mm]",
            "Gaze point X [DACS px]",
        ])
        for i in range(DURATION * 10):
            t = i / 10
            writer.writerow([
                date, clock, int(t * 1_000_000),
                f"{3.0 + 0.1 * math.sin(t):.4f}",
                f"{640 + 25 * math.cos(t):.4f}",
            ])


def run(args: list[str], cwd: Path, env: dict[str, str]) -> None:
    print("+", " ".join(args), flush=True)
    subprocess.run(args, cwd=cwd, env=env, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="exercise the indexer, signals and HTTP endpoints, then exit")
    parser.add_argument("--port", type=int, default=8777)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="cogload-synthetic-demo-") as tmp:
        workspace = Path(tmp)
        application, corpus = workspace / "application", workspace / "corpus"
        application.mkdir()
        for module in ("app.py", "index_build.py", "signals_build.py"):
            shutil.copy2(ROOT / module, application / module)
        (application / "store").mkdir()
        generate_corpus(corpus)
        env = os.environ.copy()
        env["ARC_REPO"] = str(corpus)
        run([sys.executable, "index_build.py"], application, env)
        run([sys.executable, "signals_build.py", SID], application, env)
        if args.check:
            smoke = (
                "from app import app; "
                "c=app.test_client(); "
                "assert c.get('/').status_code == 200; "
                "d=c.get('/api/index').get_json(); "
                f"assert len(d['sessions'])==1 and d['sessions'][0]['session_id']=='{SID}'; "
                "assert d['sessions'][0]['transcript_available']; "
                "assert d['sessions'][0]['labeled']; "
                f"assert c.get('/api/signals/{SID}').status_code == 200; "
                f"assert c.get('/media/{SID}/video/{CAMERA}').status_code == 200; "
                f"assert c.get('/media/{SID}/audio/{MIC}').status_code == 200; "
                f"assert c.get('/tx/{SID}').status_code == 200; "
                "print('PASS: synthetic index, media, transcript, signals and routes')"
            )
            run([sys.executable, "-c", smoke], application, env)
        else:
            print("\nAll data are fictional and generated locally. Nothing was uploaded.", flush=True)
            print(f"Open http://127.0.0.1:{args.port}/ (Ctrl+C to stop)", flush=True)
            run([sys.executable, "app.py", "--host", "127.0.0.1",
                 "--port", str(args.port)], application, env)


if __name__ == "__main__":
    main()
