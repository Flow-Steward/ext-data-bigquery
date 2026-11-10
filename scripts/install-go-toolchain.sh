#!/bin/sh
# Install the checksum-pinned official SDK before invoking any Go command.
# The Docker image is a pinned Debian/bootstrap environment, not the compiler.
set -eu
fs_go_version=go1.27.4
case "$(uname -m)" in
  aarch64|arm64)
    fs_go_arch=arm64
    fs_go_sha=3392582988a273a87ab5043ee1b2c9c17a2c3c00580bf9326c47a2e0aaaeac92
    ;;
  x86_64|amd64)
    fs_go_arch=amd64
    fs_go_sha=7226736c2339719cda28fbf3a54d71aa63bcefe376e98cef00034268dab24840
    ;;
  *) echo '[FAIL] Unsupported Go build host architecture' >&2; exit 2 ;;
esac
fs_go_filename="$fs_go_version.linux-$fs_go_arch.tar.gz"
fs_go_url="https://go.dev/dl/$fs_go_filename"
if test -n "${FS_GO_TOOLCHAIN_ARCHIVE:-}"; then
  fs_go_archive=$FS_GO_TOOLCHAIN_ARCHIVE
else
  fs_go_cache=/go/pkg/mod/cache/flow-steward-toolchains
  mkdir -p "$fs_go_cache"
  fs_go_archive="$fs_go_cache/$fs_go_filename"
  if ! test -f "$fs_go_archive"; then
    fs_go_temporary="$fs_go_archive.download.$$"
    trap 'rm -f "$fs_go_temporary"' EXIT HUP INT TERM
    wget -q -O "$fs_go_temporary" "$fs_go_url"
    fs_go_actual=$(sha256sum < "$fs_go_temporary")
    test "${fs_go_actual%% *}" = "$fs_go_sha"
    mv "$fs_go_temporary" "$fs_go_archive"
    trap - EXIT HUP INT TERM
  fi
fi
# Fail before replacing the existing compiler if the input/cache is corrupted.
fs_go_actual=$(sha256sum < "$fs_go_archive")
test "${fs_go_actual%% *}" = "$fs_go_sha"
rm -rf /usr/local/go
tar -C /usr/local -xzf "$fs_go_archive"
test "$(GOTOOLCHAIN=local go version)" = "go version $fs_go_version linux/$fs_go_arch"
if test -n "${1:-}"; then
  printf '{"version":"%s","host_arch":"%s","url":"%s","sha256":"%s"}\n' \
    "$fs_go_version" "$fs_go_arch" "$fs_go_url" "$fs_go_sha" > "$1"
fi
echo "[PASS] Official $fs_go_version $fs_go_arch SDK verified and installed"
