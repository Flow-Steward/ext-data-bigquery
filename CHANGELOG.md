# Changelog

## 1.1.10

- Rebuild Linux AMD64 and ARM64 native payloads with google.golang.org/api v0.315.0 on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the other selected dependency versions, connector controls and approved profiles; reject the superseded Google API module during package verification.

## 1.1.9

- Rebuild both Linux architectures in CI from pinned MCP Toolbox v1.17.0 and the reviewed Go 1.27.4 dependency floor.
- Preserve connector restrictions, safety patches and native build information validation; record generated provenance per build.
- Fetch the exact upstream source automatically and reject dirty source overrides.


## 1.1.8

- Clarify workflow action names, descriptions and field help; add setup and input examples. Existing action IDs, input schemas and runtime behavior are preserved.

