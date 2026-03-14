#!/usr/bin/env bash
# Build a multi-arch image locally using Docker Buildx.
# Requires: docker buildx with QEMU support installed
#
# Usage:
#   ./scripts/build-multiarch.sh             # build only (no push)
#   ./scripts/build-multiarch.sh --push      # build and push to registry
#   REGISTRY=ghcr.io/myorg ./scripts/build-multiarch.sh --push

set -euo pipefail

REGISTRY=${REGISTRY:-"ghcr.io/opendqv"}
IMAGE="${REGISTRY}/opendqv"
TAG=${TAG:-"latest"}
PLATFORMS="linux/amd64,linux/arm64"

echo "Building ${IMAGE}:${TAG} for ${PLATFORMS}"

BUILD_ARGS=(
  --platform "${PLATFORMS}"
  --tag "${IMAGE}:${TAG}"
)

if [[ "${1:-}" == "--push" ]]; then
  BUILD_ARGS+=(--push)
  echo "Will push to registry."
else
  BUILD_ARGS+=(--load)
  echo "Build only (no push). Pass --push to push."
  # --load only works for single platform; for multi-arch we need --push or --output
  # Override for local build:
  BUILD_ARGS=(--platform "linux/amd64" --tag "${IMAGE}:${TAG}" --load)
  echo "Note: local --load only supports single platform. Building amd64 only locally."
fi

docker buildx build "${BUILD_ARGS[@]}" .
echo "Done: ${IMAGE}:${TAG}"
