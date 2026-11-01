#!/bin/sh
# Pinned minimal BigQuery-only native build. Parent owns the shared caches.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
GO_IMAGE=golang:1.26.2-bookworm@sha256:47ce5636e9936b2c5cbf708925578ef386b4f8872aec74a67bd13a627d242b19
SOURCE=${TOOLBOX_SOURCE:-/private/tmp/fs-toolbox-upstream-20261101}
docker run --rm --cpus 1 --memory 2g \
 -v flow-steward-db-batch-go-cache:/go/pkg/mod -v flow-steward-db-batch-build-cache:/root/.cache/go-build \
 -v "$SOURCE:/upstream:ro" -v "$ROOT:/out" -e GO_IMAGE="$GO_IMAGE" "$GO_IMAGE" sh -c '
 set -eu
 cp -a /upstream /src
 cd /src
 test "$(git rev-parse HEAD)" = b0010060a939455d91e4f8ba62dc3e72b1892469
 git diff --exit-code
 git diff --cached --exit-code
 git apply /out/scripts/patches/0001-flowsteward-bigquery-safety.patch
 awk '\''/internal\/(sources|tools)\// {if ($2 ~ /internal\/(sources\/bigquery|tools\/bigquery\/bigquerysql)"$/) print; next} { print }'\'' cmd/internal/imports.go > /tmp/imports.go
 test "$(grep -c "internal/\(sources\|tools\)/" /tmp/imports.go)" = 2
 mv /tmp/imports.go cmd/internal/imports.go
 gofmt -w internal/sources/bigquery/bigquery.go internal/tools/bigquery/bigquerysql/bigquerysql.go
 go test -v -p 1 ./internal/sources/bigquery ./internal/tools/bigquery/bigquerycommon ./internal/tools/bigquery/bigquerysql -count=1
 export SOURCE_DATE_EPOCH=1779400183
 for arch in amd64 arm64; do
  mkdir -p /out/bin/linux-$arch
  CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -p 1 -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.bigquery -X github.com/googleapis/mcp-toolbox/cmd.commitSha=b0010060a939455d91e4f8ba62dc3e72b1892469" -o /out/bin/linux-$arch/toolbox .
  chmod 0755 /out/bin/linux-$arch/toolbox
  echo "[PASS] linux-$arch $(stat -c %s /out/bin/linux-$arch/toolbox) bytes"
 done
 '
python3 - "$ROOT" "$GO_IMAGE" <<'PY'
from pathlib import Path
import hashlib,json,sys
r=Path(sys.argv[1]);patch='scripts/patches/0001-flowsteward-bigquery-safety.patch'
x={'tag':'v1.3.0','commit':'b0010060a939455d91e4f8ba62dc3e72b1892469','go_image':sys.argv[2],'source_date_epoch':1779400183,'patch':patch,'patch_sha256':hashlib.sha256((r/patch).read_bytes()).hexdigest(),'packages':['github.com/googleapis/mcp-toolbox/internal/sources/bigquery','github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerysql'],'binaries':{}}
for target in ('linux-amd64','linux-arm64'):
 p=r/'bin'/target/'toolbox';x['binaries'][target]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size}
(r/'toolbox-provenance.json').write_text(json.dumps(x,indent=2)+'\n')
PY
