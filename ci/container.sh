#!/bin/sh
# Run a command in a Debian trixie container with the build dependencies installed and the
# repository mounted at /src, e.g. 'ci/container.sh ci/check.sh' or 'ci/container.sh make deb'.
# Extra 'docker run' arguments can be passed in CI_DOCKER_ARGS.
set -eu
top=$(cd "$(dirname "$0")/.." && pwd)
# shellcheck disable=SC2086
exec docker run --rm ${CI_DOCKER_ARGS:-} -v "$top:/src" -w /src debian:trixie \
    sh -c 'ci/deps.sh && exec "$@"' sh "$@"
