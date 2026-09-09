# Mimir Core on Linux + NVIDIA CUDA.
#
# Build from the REPOSITORY ROOT, not this directory -- the image needs
# backend/, and a build context cannot reach above itself:
#
#   docker build -f cloud/Dockerfile -t mimir-core:cuda .
#   docker run --rm --gpus all mimir-core:cuda            # diagnostic
#   docker run --rm --gpus all -v /path/to/clips:/data:ro \
#       mimir-core:cuda scan --input /data --output /tmp/out
#
# Deliberately not a PyInstaller build. The Windows app freezes the Core into
# sidecars because it ships to machines with no Python; a container already is
# the runtime, so freezing would add a build step, an opaque binary and a whole
# class of missing-hidden-import failures for nothing.

# cuDNN is required, not optional: onnxruntime-gpu links against it, and the
# plain `runtime` image without cuDNN gets you a CUDAExecutionProvider that
# reports itself available and then fails to initialise -- which the detector
# handles by silently falling back to CPU. That failure mode is the whole
# reason mimir_core_v2.doctor exists.
#
# Pin the CUDA minor version. onnxruntime-gpu 1.23 is built against CUDA 12.x
# and cuDNN 9; if you move either, re-run the diagnostic before trusting it.
FROM nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ffmpeg for decoding. libGL/libglib are NOT installed on purpose: the cloud
# requirements use opencv-python-headless, which does not need them, and their
# absence means a mistaken switch back to plain opencv-python fails loudly at
# image build rather than quietly at the first video.
RUN apt-get update && apt-get install --no-install-recommends -y \
        python3 \
        python3-pip \
        python3-venv \
        ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# A venv, so pip is not fighting Ubuntu's externally-managed python3.
ENV VIRTUAL_ENV=/opt/mimir
RUN python3 -m venv "$VIRTUAL_ENV"
ENV PATH="$VIRTUAL_ENV/bin:$PATH"

WORKDIR /app

# Requirements first, so the layer that installs ~2 GB of CUDA wheels is not
# rebuilt every time a line of Python changes.
COPY backend/requirements-core-common.txt backend/requirements-core-cloud.txt ./backend/
RUN pip install --upgrade pip && pip install -r backend/requirements-core-cloud.txt

COPY backend/mimir_core_v2 ./backend/mimir_core_v2
COPY backend/mimir_core_v2_scan.py ./backend/

# The ONNX weights only. backend/models/ also holds rf-detr-nano.pth, a 349 MB
# training checkpoint that inference never touches -- copying the directory
# would triple this layer to ship a file the container cannot use.
# model_manifest.json travels with mimir_core_v2/ above and points here.
COPY backend/models/rfdetr-small.onnx ./backend/models/rfdetr-small.onnx

ENV PYTHONPATH=/app/backend

# Where Core writes. Mounted as a volume in production so a job's frames and
# temporary video never touch the image's writable layer.
ENV MIMIR_OUTPUT_ROOT=/var/mimir
RUN mkdir -p /var/mimir /tmp/mimir

# Nothing here needs root once the wheels are installed. Uploaded video is
# untrusted input and gets decoded by ffmpeg, which is a large C surface.
RUN useradd --system --create-home --uid 10001 mimir \
    && chown -R mimir:mimir /var/mimir /tmp/mimir
USER mimir

# Fails the container when the detector resolved to CPU, rather than letting it
# join a queue and bill GPU time for CPU work.
HEALTHCHECK --interval=60s --timeout=30s --start-period=30s --retries=2 \
    CMD python -m mimir_core_v2.doctor --require-gpu --json > /dev/null || exit 1

ENTRYPOINT ["python"]
CMD ["-m", "mimir_core_v2.doctor"]
