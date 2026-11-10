#!/bin/sh
set -eu
cp -a /upstream /src
cd /src
git apply /out/scripts/patches/0000-security-baseline.patch
printf '%s\n' '1c59a76307271df0a6992d9db406f9c49ba670dfb898e029bbc86136adcc9294  go.mod' 'd0e98fac8a9d4677bc239a668b472dd2f459ab65f3d0fec7ceb2a50bb6138841  go.sum' | sha256sum -c -
export GOFLAGS=-mod=readonly GOTOOLCHAIN=local GOMAXPROCS=2
test "$(go version | cut -d ' ' -f 3)" = go1.27.3
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
export SOURCE_DATE_EPOCH=1793908560
for target in linux-amd64 linux-arm64; do
 arch=${target#linux-}
 mkdir -p /out/bin/$target
 CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -p 1 -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.bigquery -X github.com/googleapis/mcp-toolbox/cmd.commitSha=06ea49d9a487a72c22419b5a8d5c22c11f0d7485" -o /out/bin/$target/toolbox .
 chmod 0755 /out/bin/$target/toolbox
 echo "[PASS] $target $(stat -c %s /out/bin/$target/toolbox) bytes"
done
sha256sum -c /tmp/fs-module-lock
