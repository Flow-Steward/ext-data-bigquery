#!/bin/sh
set -eu
sh /out/scripts/install-go-toolchain.sh /out/scripts/go-toolchain-selected.json
cp -a /upstream /src
cd /src
git apply /out/scripts/patches/0000-security-baseline.patch
printf '%s\n' 'a3e5770d5ba7db60ead3c730193aeebce7480dd4af8774c14c52e469e944fadb  go.mod' 'f4f44a249a83d87aeb481f56cd6769f093b064d7b3dca79a455fe4baa4210c79  go.sum' | sha256sum -c -
export GOFLAGS=-mod=readonly GOTOOLCHAIN=local GOMAXPROCS=2
test "$(go version | cut -d ' ' -f 3)" = go1.27.4
git apply /out/scripts/patches/0000-approved-prebuilt-profiles.patch
git apply /out/scripts/patches/0001-flowsteward-bigquery-safety.patch
cat > cmd/internal/imports.go <<'IMPORTS'
package internal
import (
	_ "github.com/googleapis/mcp-toolbox/internal/sources/bigquery"
	_ "github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerysql"
	_ "github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerylisttableids"
	_ "github.com/googleapis/mcp-toolbox/internal/tools/bigquery/bigquerygettableinfo"
)
IMPORTS
# Literal registrations: no prompts/resources or unapproved database tools.
test "$(grep -c 'internal/' cmd/internal/imports.go)" = 4
sha256sum go.mod go.sum > /tmp/fs-module-lock
go test -p 1 ./internal/sources/bigquery ./internal/tools/bigquery/bigquerycommon ./internal/tools/bigquery/bigquerysql ./internal/tools/bigquery/bigquerylisttableids ./internal/tools/bigquery/bigquerygettableinfo -count=1
export SOURCE_DATE_EPOCH=1795211695
for target in linux-amd64 linux-arm64; do
 arch=${target#linux-}
 mkdir -p /out/bin/$target
 CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -p 1 -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.bigquery -X github.com/googleapis/mcp-toolbox/cmd.commitSha=24762d91babd43d91afc3daeeece1cb446266c54" -o /out/bin/$target/toolbox .
 chmod 0755 /out/bin/$target/toolbox
 echo "[PASS] $target $(stat -c %s /out/bin/$target/toolbox) bytes"
done
sha256sum -c /tmp/fs-module-lock
