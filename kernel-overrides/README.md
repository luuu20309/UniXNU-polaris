# Common XNU patch overrides

`xnu/offline-component-load-probes.patch` is based on UniXNU commit
`37987aea410691df3c1e0e146a1bfd6127932149`, under the repository's retained
BSD-2-Clause-Patent license. The only change guards the
`offline-legacy-register` probe with `XNU_NATIVE_LEGACY_DISPLAY`, matching
the existing guard for the same provider in `IOUserClient.cpp`. Otherwise a
platform kernel with display disabled (as polaris at this stage) still
references the absent archive.

`scripts/build-platform-kernel.py` copies the complete pinned common patch
set into its output directory and overlays these files. It never edits the
base checkout or replaces the other common patches. Use that wrapper for
this source-kernel milestone; the upstream full prelink/firmware command is
not qualified for polaris yet.
