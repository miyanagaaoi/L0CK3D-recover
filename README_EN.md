# C3RB3R (.L0CK3D) Ransomware · Btrfs Keyless Recovery — Case Report & Toolkit

> English overview of a sanitized post-mortem for a C3RB3R ransomware incident on a Synology DSM + Btrfs NAS, with a reusable recovery toolkit and an AI-executable runbook.
>
> All IPs, accounts, passwords, hostnames, organization/person names are sanitized. Statistics are preserved.

**Language**: [简体中文](README.md) | English

---

## At a glance

| Item | Value |
|---|---|
| Target | Synology DSM 7.x, Btrfs volumes (one CoW, one `+C`/NoCOW) |
| Family | C3RB3R (`.L0CK3D`, `read-me3.txt`) |
| Encrypted | **1,295,238 files / 2,981 GB** (business data; excludes `#recycle` and `@docker` image layers) |
| Recovery window | 1–2 days after encryption, source kept read-only |
| **Recovered** | **73,809 files / ≈351.7 GB** |
| File-level rate | **9.42%** (excluding @synologydrive; 7.88% including) |
| Byte-level rate | **21.37%** (excluding @synologydrive; 13.70% including) |
| Methods | CoW old-extent (delta + precise EXTENT_DATA), free-space carving, Btrfs CRC32C csum verification |

## Repository layout

```
.
├── README.md / README_EN.md      Overview (CN / EN)
├── GITHUB_GUIDE.md               Open-source usage guide (CN): quick start, skill integration, tool/AI choice, cautions, Chinese writing, references
├── AGENT_RUNBOOK.md              AI-executable runbook (phases, thresholds, failure branches)
├── case_summary.json             Machine-readable summary
├── manifest.json                 File & toolkit manifest
├── 01_incident.md                Incident timeline
├── 02_forensics.md               C3RB3R format + Btrfs CoW/NoCOW principles
├── 03_recovery_methods.md        Five recovery methods & measured results
├── 04_lessons.md                 Lessons, failure boundaries, decision table, pitfalls
├── 05_report_template.md         Recovery report template
├── SECURITY.md / LICENSE / CHANGELOG.md / CONTRIBUTING.md
├── examples/                     Example config & commands
├── .github/                      CI workflow, issue/PR templates
└── toolkit/                      Sanitized scripts + tool manifest
```

## Quick start (5 steps)

1. **Preserve the scene**: stop all writes; do NOT reboot, reinstall, delete `.L0CK3D`, or run `btrfs balance/scrub/defrag`.
2. **Check environment**: `btrfs-progs`, Python ≥ 3.8, confirm CoW/NoCOW via `lsattr -d <SHARE>`.
3. **Survey**: `python3 toolkit/scan_recoverable.py --root <SHARE> --out scan.json`.
4. **Sample-verify**: recover 10–30 files; only proceed to full run when success ≥ 70%.
5. **Full run + verify**: recover to a *separate* target volume; validate every file (structure/decoding; ideally Btrfs csum).

> Read `AGENT_RUNBOOK.md` before executing anything. Never write to the source volume.

## Hard limits (verified in this case)

- `0x24` (< 1 MB, fully encrypted): old extents freed → only images/PDF/ZIP/OLE can be carved, mostly unrecoverable;
- `+C` (NoCOW) shares: data written **after the last snapshot** is overwritten in place → unrecoverable;
- Database files: cannot rebuild consistency even with fragments;
- Old-root bookend: works only when old roots/leaves are parseable and extents are not overwritten.

## References

- https://github.com/soimy/c3rb3r-rescue
- https://github.com/soimy/c3rb3r-rescue/issues/12
- https://github.com/sunkangbo121-ship-it/c3rb3r-btrfs-extent-rescue
- https://github.com/crt106/c3rb3r-btrfs-recovery
- https://btrfs.readthedocs.io/

## License & disclaimer

MIT (see `LICENSE`). Provided "as is", no guarantee of recovery for any specific scene. Use only on data you are authorized to process.
