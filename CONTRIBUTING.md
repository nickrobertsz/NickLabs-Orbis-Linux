# Contributing to NickLabs PS4 Orbis Linux

1. **Evidence before claims.** A capability changes state in `manifests/capability-matrix.json` only with evidence. Upstream evidence makes it `UPSTREAM_REPORTED` with a pinned source. An Orbis hardware receipt from `tests/hardware/qualify.sh` makes it `VERIFIED`. "It compiles" is never "it works on a PS4".
2. **Pin everything.** New upstream inputs go in `manifests/sources.json` with a commit, tag object or SHA-256. Imported patches need a provenance entry: origin, author, licence, why, and how it was checked.
3. **Never redistribute firmware.** Add hashes to `manifests/firmware.json`; never add the files themselves.
4. **Jaguar ISA.** No `-march=native` or x86-64-v3. Run `orbis isa-audit` on anything that produces binaries.
5. **Tests.** Run `make test` before sending changes. Engine and boot-file logic must stay testable against fixture roots (`ORBIS_ROOT`).
6. **GPU bugs.** Attach an `orbis-infer-harness report` (or the `orbis doctor --deep --json` output) and the `steps/*/kmsg.log` of the failing step. Say which console (CUH model, southbridge), kernel and Mesa you used.
7. **Credits.** Keep authorship and licences of imported work. Update `docs/UPSTREAM-CREDITS.md`.

Bug reports: use `.github/ISSUE_TEMPLATE/hardware-report.md`.
