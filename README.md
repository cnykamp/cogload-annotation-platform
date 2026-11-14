# ARC-CogLoad Multimodal Research Annotation Platform

A research annotation application for navigating multimodal cognitive-load
experiments, including time-aligned video, audio-derived transcripts, recorded
signals, and experimental annotations.

## Overview

The Python application has three principal components:

- `app.py`: researcher-facing annotation application and web interface.
- `index_build.py`: canonical corpus indexing, session metadata, annotations,
  stream inventories, and synchronization offsets.
- `signals_build.py`: derived-signal processing used by the annotation platform.

The indexer normalizes heterogeneous session identifiers and time references onto
one declared master timeline. Only synchronization offsets marked as trusted are
accepted for correction, avoiding unintended shifts from weak estimates.

## Data and setup

This code-only release **does not include** experimental recordings, participant
data, transcripts, physiological datasets, or the separately maintained corpus.
The application is research software and requires authorized access to its
underlying data environment to operate against real sessions.

The indexer reads the corpus from the `ARC_REPO` environment variable (or its
relative fallback). Additional dependencies and runtime configuration should be
confirmed against the source modules before attempting to deploy.

## Contributions and attribution

Caleb Nykamp was the primary software developer (approximately 90% of the
implementation), building on an initial research setup provided by Alvaro Vega
Hidalgo as part of the University of Michigan ARC-CogLoad research project.
Reproduced with the original project supervisor's permission.

## Disclosure

No open-source license has been granted in this repository. Permission to
redistribute or adapt this code beyond the authorized release should be
confirmed with the project owners.
