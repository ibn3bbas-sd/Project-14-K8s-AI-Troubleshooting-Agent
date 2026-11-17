#!/usr/bin/env bash
# Build both images for linux/amd64 and push them to Huawei Cloud SWR.
#
# Usage:
#   export SWR_REGISTRY=swr.me-east-1.myhuaweicloud.com   # your region's SWR
#   export SWR_ORG=my-org                                  # SWR organization
#   docker login -u '<region>@<AK>' -p '<login key>' "$SWR_REGISTRY"
#   k8s/scripts/build-push.sh [tag]
#
# Kunpeng (arm64) node pools: set PLATFORM=linux/arm64.
set -euo pipefail

: "${SWR_REGISTRY:?set SWR_REGISTRY, e.g. swr.me-east-1.myhuaweicloud.com}"
: "${SWR_ORG:?set SWR_ORG to your SWR organization}"
TAG="${1:-$(git rev-parse --short HEAD 2>/dev/null || echo latest)}"
PLATFORM="${PLATFORM:-linux/amd64}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

for app in backend frontend; do
  dir="$ROOT/agent-backend"; [ "$app" = frontend ] && dir="$ROOT/agent-interface"
  image="$SWR_REGISTRY/$SWR_ORG/ai-agent-$app"
  echo "==> $image:$TAG"
  docker buildx build --platform "$PLATFORM" -t "$image:$TAG" -t "$image:latest" --push "$dir"
done

echo
echo "Pin the tag in k8s/kustomization.yaml:"
echo "  (cd k8s && kustomize edit set image \\"
echo "     ai-agent-backend=$SWR_REGISTRY/$SWR_ORG/ai-agent-backend:$TAG \\"
echo "     ai-agent-frontend=$SWR_REGISTRY/$SWR_ORG/ai-agent-frontend:$TAG)"
