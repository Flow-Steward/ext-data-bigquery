#!/bin/sh
# Pinned minimal BigQuery-only native build. Parent owns the shared caches.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DEFAULT_GO_IMAGE=golang:1.26.2-bookworm@sha256:47ce5636e9936b2c5cbf708925578ef386b4f8872aec74a67bd13a627d242b19
GO_IMAGE=${GO_IMAGE:-$DEFAULT_GO_IMAGE}
: "${FS_BIGQUERY_BUILD_NAME:?Parent must set a unique build container name}"
: "${FS_BIGQUERY_GO_MOD_CACHE:?Parent must set an owned module cache volume}"
: "${FS_BIGQUERY_GO_BUILD_CACHE:?Parent must set an owned build cache volume}"
SOURCE=${TOOLBOX_SOURCE:-/private/tmp/fs-toolbox-upstream-20261101}
docker run --rm --name "$FS_BIGQUERY_BUILD_NAME" --cpus 1 --memory 2g \
 -v "$FS_BIGQUERY_GO_MOD_CACHE:/go/pkg/mod" -v "$FS_BIGQUERY_GO_BUILD_CACHE:/root/.cache/go-build" \
 -v "$SOURCE:/upstream:ro" -v "$ROOT:/out" -e GO_IMAGE="$GO_IMAGE" "$GO_IMAGE" sh -c '
 set -eu
 report_metrics() {
  fs_native_status=$?
  trap - EXIT
  echo "[metrics] memory.peak_bytes"
  cat /sys/fs/cgroup/memory.peak || true
  echo "[metrics] memory.events"
  cat /sys/fs/cgroup/memory.events || true
  exit "$fs_native_status"
 }
 trap report_metrics EXIT
 cp -a /upstream /src
 cd /src
 test "$(git rev-parse HEAD)" = b0010060a939455d91e4f8ba62dc3e72b1892469
 git diff --exit-code
 git diff --cached --exit-code
 # Demonstrate native metadata regressions on the unmodified pinned tools.
 git apply /out/scripts/patches/0002-bigquery-metadata-red-tests.patch
 gofmt -w internal/tools/bigquery/bigquerylisttableids/flowsteward_metadata_test.go internal/tools/bigquery/bigquerygettableinfo/flowsteward_metadata_test.go
 set +e
 go test -v -p 1 ./internal/tools/bigquery/bigquerylisttableids ./internal/tools/bigquery/bigquerygettableinfo -run "TestFlowStewardMetadataScopeBeforeWire|TestFlowStewardBoundedMetadataWire|TestFlowStewardPaginationBound" -count=1 > /tmp/metadata-red.log 2>&1
 red_status=$?
 set -e
 cat /tmp/metadata-red.log
 test "$red_status" -ne 0
 grep -q "scope escaped" /tmp/metadata-red.log
 grep -q "unbounded catalog request" /tmp/metadata-red.log
 grep -q "unexpected extra page" /tmp/metadata-red.log
 if grep -q "build failed" /tmp/metadata-red.log; then exit 1; fi
 echo "[PASS] native metadata RED: scope, bounded request, overflow pagination failed as expected"
 rm internal/tools/bigquery/bigquerylisttableids/flowsteward_metadata_test.go internal/tools/bigquery/bigquerygettableinfo/flowsteward_metadata_test.go
 git apply /out/scripts/patches/0001-flowsteward-bigquery-safety.patch
 awk '\''/internal\/(sources|tools)\// {if ($2 ~ /internal\/(sources\/bigquery|tools\/bigquery\/(bigquerysql|bigquerylisttableids|bigquerygettableinfo))"$/) print; next} { print }'\'' cmd/internal/imports.go > /tmp/imports.go
 test "$(grep -c "internal/\(sources\|tools\)/" /tmp/imports.go)" = 4
 mv /tmp/imports.go cmd/internal/imports.go
 gofmt -w internal/sources/bigquery/*.go internal/tools/bigquery/bigquerysql/*.go internal/tools/bigquery/bigquerylisttableids/*.go internal/tools/bigquery/bigquerygettableinfo/*.go
 go test -v -p 1 ./internal/sources/bigquery ./internal/tools/bigquery/bigquerycommon ./internal/tools/bigquery/bigquerysql ./internal/tools/bigquery/bigquerylisttableids ./internal/tools/bigquery/bigquerygettableinfo -count=1
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
x={'tag':'v1.3.0','commit':'b0010060a939455d91e4f8ba62dc3e72b1892469','go_image':sys.argv[2],'source_date_epoch':1779400183,'patch':patch,'patch_sha256':hashlib.sha256((r/patch).read_bytes()).hexdigest(),'packages':['github.com/googleapis/mcp-toolbox/internal/sources/bigquery','github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerysql','github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerylisttableids','github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerygettableinfo'],'binaries':{}}
for target in ('linux-amd64','linux-arm64'):
 p=r/'bin'/target/'toolbox';x['binaries'][target]={'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'size':p.stat().st_size}
(r/'toolbox-provenance.json').write_text(json.dumps(x,indent=2)+'\n')
(r/'SHA256SUMS').write_text(''.join(record['sha256']+'  bin/'+target+'/toolbox\n' for target,record in x['binaries'].items()))
PY
