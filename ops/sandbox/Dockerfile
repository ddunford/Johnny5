# syntax=docker/dockerfile:1.9
#
# Johnny 5 — code_exec SANDBOX image. Runs HOSTILE Python; the container is the
# ONLY trust boundary. Built by `./ctl.sh sandbox-build`; launched per-exec by
# ops/sandbox/run-sandbox.sh (which applies --network none, --cap-drop ALL,
# resource caps, read-only rootfs, non-root, hard timeout).
#
# Deliberately minimal and isolated from the app image: NO project code, NO
# secrets, NO extra packages, NO build tools. Nothing in here is trusted with
# anything — if the boundary holds, the contents don't matter.

FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONHASHSEED=0 \
    PYTHONNOUSERSITE=1 \
    HOME=/tmp

# The only thing we add: the runner entrypoint (stdin -> JSON verdict).
COPY runner.py /opt/runner.py

# Run as the unprivileged nobody:nogroup (65534) baked into the base image. The
# launcher also passes --user 65534:65534, so root is never an option even if
# this line were dropped.
USER 65534:65534
WORKDIR /tmp

ENTRYPOINT ["python", "/opt/runner.py"]
