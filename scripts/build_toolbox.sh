#!/bin/sh
# Reviewed v1.16.0 source, exact Go1.27.3 graph, and connector-only registrations.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
GO_IMAGE=golang:1.27.3-bookworm@sha256:dc0963f32053e4bc451d24d87e3892d42d01021b61f093e98eb95c0352bb84b0
TOOLBOX_COMMIT=06ea49d9a487a72c22419b5a8d5c22c11f0d7485
SOURCE=${TOOLBOX_SOURCE:-/private/tmp/fs-updates-95-20261110/investigation/toolbox}
test "$(git -C "$SOURCE" rev-parse HEAD)" = "$TOOLBOX_COMMIT"
git -C "$SOURCE" diff --exit-code
git -C "$SOURCE" diff --cached --exit-code
docker run --rm --name "fs-updates95-bigquery-$$" --label flow-steward.audit=remaining-updates-20261110 --cpus 2 --memory 4g \
 -v "$SOURCE:/upstream:ro" -v "$ROOT:/out" \
 -v flow-steward-db-batch-go-cache:/go/pkg/mod -v flow-steward-db-batch-build-cache:/root/.cache/go-build \
 "$GO_IMAGE" sh /out/scripts/build_inside.sh
python3 "$ROOT/scripts/provenance.py"
python3 "$ROOT/scripts/native_build_info.py"

# Native suite: go test -p 1 ./internal/sources/bigquery ./internal/tools/bigquery/bigquerycommon ./internal/tools/bigquery/bigquerysql ./internal/tools/bigquery/bigquerylisttableids ./internal/tools/bigquery/bigquerygettableinfo
# CGO_ENABLED=0; -trimpath -buildvcs=false; linux-amd64 linux-arm64
# github.com/googleapis/mcp-toolbox/internal/sources/bigquery
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerysql
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerylisttableids
# github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerygettableinfo
