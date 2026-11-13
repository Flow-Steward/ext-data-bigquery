#!/bin/sh
# Reviewed v1.17.0 source, reviewed module graph; official Go1.27.4 installed over bootstrap, and connector-only registrations.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
BOOTSTRAP_IMAGE=golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0
TOOLBOX_COMMIT=45ed205cc8682ed066c82e0b65bcd0e8fc3becce
SOURCE=${TOOLBOX_SOURCE:-${FS_TOOLBOX_SOURCE_DIR:-}}
TEMP_SOURCE=
cleanup() { if test -n "$TEMP_SOURCE"; then rm -rf -- "$TEMP_SOURCE"; fi; }
trap cleanup EXIT HUP INT TERM
if test -z "$SOURCE"; then
 TEMP_SOURCE=$(mktemp -d "${TMPDIR:-/tmp}/fs-toolbox-source.XXXXXX")
 git clone --quiet --depth 1 --branch v1.17.0 https://github.com/googleapis/mcp-toolbox.git "$TEMP_SOURCE/upstream"
 SOURCE=$TEMP_SOURCE/upstream
fi
SOURCE=$(CDPATH= cd -- "$SOURCE" && pwd)
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$TOOLBOX_COMMIT" || { echo "Unreviewed Toolbox source commit" >&2; exit 1; }
if test -n "$(git -C "$SOURCE" status --porcelain --untracked-files=all --ignored)"; then
 echo "Toolbox source must be clean, including untracked and ignored files" >&2
 exit 1
fi
docker run --rm --name "fs-toolbox-bigquery-$$" --label flow-steward.audit=dependency-refresh-20261113 --cpus 2 --memory 4g \
 -v "$SOURCE:/upstream:ro" -v "$ROOT:/out" \
 -v flow-steward-db-batch-go-cache:/go/pkg/mod -v flow-steward-db-batch-build-cache:/root/.cache/go-build \
 "$BOOTSTRAP_IMAGE" sh /out/scripts/build_inside.sh
python3 "$ROOT/scripts/provenance.py"
python3 "$ROOT/scripts/native_build_info.py"

# Native suite: go test -p 1 ./internal/sources/bigquery ./internal/tools/bigquery/bigquerycommon ./internal/tools/bigquery/bigquerysql ./internal/tools/bigquery/bigquerylisttableids ./internal/tools/bigquery/bigquerygettableinfo
# CGO_ENABLED=0; -trimpath -buildvcs=false; linux-amd64 linux-arm64
# github.com/googleapis/mcp-toolbox/internal/sources/bigquery
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerysql
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerylisttableids
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerygettableinfo
