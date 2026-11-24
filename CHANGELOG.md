# Changelog

## 1.1.14 - 2026-11-24

- Update the pinned Toolbox source to v1.18.0 and refresh the reviewed Linux AMD64 and ARM64 dependency locks.
- Update Cloud SQL Connector, Spanner, LZ4 and the OpenTelemetry Auto SDK in the build graph.
- Update development PyYAML to 6.0.4.

## 1.1.13 - 2026-11-21

- Refresh the reviewed Toolbox Go dependency lock and rebuild both Linux runtime targets.

## 1.1.12

- Rebuild Linux AMD64 and ARM64 native payloads with grpc-gateway v2.34.0 and its required YAML v4 and Go tooling module updates.
- Preserve pinned Toolbox v1.17.0, verified Go 1.27.4, database safety controls and approved profiles.

## 1.1.11

### Added

- Added a Support tab with links for reporting a problem, asking a question or requesting a feature in this extension's GitHub repository, and what to include in a report.

## 1.1.10

- Rebuild Linux AMD64 and ARM64 native payloads with google.golang.org/api v0.315.0 on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the other selected dependency versions, connector controls and approved profiles; reject the superseded Google API module during package verification.

## 1.1.9

- Rebuild both Linux architectures in CI from pinned MCP Toolbox v1.17.0 and the reviewed Go 1.27.4 dependency floor.
- Preserve connector restrictions, safety patches and native build information validation; record generated provenance per build.
- Fetch the exact upstream source automatically and reject dirty source overrides.


## 1.1.8

- Clarify workflow action names, descriptions and field help; add setup and input examples. Existing action IDs, input schemas and runtime behavior are preserved.

