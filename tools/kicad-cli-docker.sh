#!/bin/sh
# Run kicad-cli from the official KiCad Docker image. The working directory is mounted at
# the same path, so relative and absolute paths under it work unchanged.
# KICAD_DOCKER_PLATFORM=linux/amd64 selects the image architecture on arm64 hosts.
# KICAD_DOCKER_CMD runs another command (such as sh) in the image instead of kicad-cli.
exec docker run --rm ${KICAD_DOCKER_PLATFORM:+--platform "$KICAD_DOCKER_PLATFORM"} \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e IRIDIUM_MODULES_3DMODEL_DIR \
  -v "$PWD:$PWD" -w "$PWD" "${KICAD_IMAGE:-kicad/kicad:10.0}" "${KICAD_DOCKER_CMD:-kicad-cli}" "$@"
