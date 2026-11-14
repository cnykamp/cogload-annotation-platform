# ARC-CogLoad Multimodal Research Annotation Platform

A Python/Flask research application for reviewing time-aligned video, audio-derived transcripts, physiological streams, and cognitive-load annotations. The app provides a completeness dashboard, synchronized video verification, segment-boundary editing, transcript curation, and a multi-signal timeline.

> **Research prototype, not a hosted service.** This repository contains application source only, not study participant data. The supplied demo fabricates its own corpus; no access to the original lab datasets is necessary to evaluate it.

## Try it with synthetic data

**Prerequisites:** Python 3.10+, [FFmpeg](https://ffmpeg.org/download.html) available on your PATH, and a local terminal. FFmpeg is needed only to generate a short fictional test-pattern video.

On macOS, install FFmpeg with \`brew install ffmpeg\` if needed.

\`\`\`bash
git clone https://github.com/cnykamp/cogload-annotation-platform.git
cd cogload-annotation-platform

python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

# Build a completely synthetic dataset, run the *real* index and signal
# pipelines, then start the app bound to localhost.
python demo/run_demo.py
\`\`\`

Open **http://127.0.0.1:8777/**, then try the example session **P99_S01_01** through the dashboard, **/sync/P99_S01_01**, **/seg/P99_S01_01**, **/tx/P99_S01_01**, and **/timeline/P99_S01_01**.

The fabricated example contains an FFmpeg test-pattern video, synthesized audio tones, a mock transcript, annotation intervals, synthetic camera-motion/eye-blink values, simulated fNIRS timestamps and signals, a biosensor channel, and synthetic gaze samples. **These are illustrative fixtures, not measured or scientifically validated signals.** In particular, the demo transcript is placeholder text rather than real speech-recognition output.

The generated corpus, index, and annotation writes exist only in a temporary workspace. Stopping the demo deletes the workspace; it does not overwrite your local research data or commit participant files.

Run its built-in smoke check without starting a web server:

\`\`\`bash
python demo/run_demo.py --check
\`\`\`

The check exercises the index builder, signal builder, and primary Flask endpoints with locally generated data. It is a **basic integration smoke test**, not full scientific validation or a comprehensive browser test.

On Windows, activate the environment with \`.venv\Scripts\activate\` or use the corresponding Python executable.

## Architecture

| Module | Purpose |
| --- | --- |
| \`app.py\` | Flask application: session API, researcher dashboard, synchronized playback, segmentation editor, transcript review, signal timeline and annotation export |
| \`index_build.py\` | Canonical session index: corpus inventory, stream statuses, annotations, recording time references, trusted camera synchronization offsets |
| \`signals_build.py\` | Derived signals aligned to a master clock: video motion, audio amplitude, simulated or real physiological streams, jitter and drift diagnostics |
| \`demo/run_demo.py\` | Independent synthetic dataset generator, isolated demo launcher and HTTP smoke check |

The time-coordinate convention is an explicit **ROS bag global-start master timeline**, with media-local zero at the selected clock stream's first frame. A recording's ROS-start offset translates between these timelines. Fine visual synchronization estimates are applied only if they carry a trusted \`ok\` or \`reference\` status. Raw-data incompleteness is distinguished from streams absent by rig design.

Human corrections are stored with absolute master-timeline nanosecond timestamps to avoid ambiguity across media streams. Quality checks must account for dataset-specific ground truth, not only interface behavior.

## Use with an authorized research corpus

The original multi-modal corpus is maintained separately and is not included here. For an authorized local installation, the code expects roughly:

\`\`\`text
<research-root>/
  data/
  data_synced/<participant>/<session>/
    sync_manifest.json
    video/*.mp4
    audio/*.wav
    csv/*.csv
  features/...
\`\`\`

The exact underlying tree and optional features vary by session. To build your index and run the app against **data you are authorized to process**:

\`\`\`bash
export ARC_REPO="/absolute/path/to/authorized/research-root"
mkdir -p store
python index_build.py
# Replace with an actual session ID printed by the indexer:
python signals_build.py PXX_SYY_ZZ
python app.py --host 127.0.0.1 --port 8777
\`\`\`

The \`ARC_MEDIA\` variable can override the default media root (normally \`$ARC_REPO/data_synced\`).

**Important:** The app loads \`store/index.json\` at import time; generate the index before launching. The signal builder runs separately for each session. Avoid pointing the demo at your real corpus; it is intentionally isolated.

### Security and limitations

- **Do not expose this development server to the public internet.** There is no production authentication, access control or hardened deployment configuration for sensitive research data.
- Keep all real participant recordings, annotations, transcriptions, derived features, credentials, and exported outputs in approved study storage, outside this repository.
- No original experimental material, participant identifiers, original transcripts, or identifiable recordings are required for the synthetic demo.
- No scientific accuracy claims should be inferred from synthetic values; the smoke test only checks that the application can execute core paths.
- Media availability and specialized sensor feature coverage depend on the installed corpus. Some missing streams are expected for particular rig configurations.

## Attribution and ownership

Caleb Nykamp was the primary software developer (approximately **90% of the implementation**), building on an initial setup provided by **Alvaro Vega Hidalgo** as part of the University of Michigan ARC-CogLoad research project. The application code is reproduced with the original project supervisor's permission.

**No open-source license is granted by this README.** Any public release, use, or redistribution must stay within the project owners' permission and the institution's research-data rules. The original lab repository and dataset remain separate.
