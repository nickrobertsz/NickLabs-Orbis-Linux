#!/usr/bin/env python3
"""Emit an SPDX 2.3 JSON SBOM for the Orbis source release from the pinned manifests."""
import hashlib
import json
import sys
import time
from pathlib import Path

R = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(R / "platform"))
import orbis  # noqa: E402


def pkg(spdxid, name, version, url, license_, comment="", checksum=None):
    p = {"SPDXID": f"SPDXRef-{spdxid}", "name": name, "versionInfo": version or "NOASSERTION",
         "downloadLocation": url or "NOASSERTION", "licenseConcluded": license_ or "NOASSERTION",
         "licenseDeclared": license_ or "NOASSERTION", "copyrightText": "NOASSERTION", "filesAnalyzed": False}
    if comment:
        p["comment"] = comment
    if checksum:
        p["checksums"] = [{"algorithm": "SHA256", "checksumValue": checksum}]
    return p


def main():
    s = json.loads((R / "manifests/sources.json").read_text())
    pkgs = [pkg("orbis", "nicklabs-ps4-orbis-linux", orbis.__version__, "NOASSERTION", "MIT",
                "Orbis platform layer, tools, recipes and documentation")]
    k = s["kernel"]["aeolia-belize-6.15"]
    pkgs.append(pkg("kernel-ps4", "ps4-linux-12xx (feeRnt)", f"{k['tag']}@{k['commit']}", k["repo"], "GPL-2.0-only",
                    "built firmware-free with Orbis patches kernel/patches/aeolia-belize-6.15"))
    m = s["mesa"]
    pkgs.append(pkg("mesa", "Mesa", m["version"], m["archive"], "MIT", "patches: graphics/patches/mesa", m["sha256"]))
    for i, p in enumerate(json.loads((R / "graphics/patches/mesa/provenance.json").read_text())["patches"]):
        pkgs.append(pkg(f"mesa-patch-{i}", p["file"], p["sha256"][:12], p["from"], p["license"].split(" ")[0], p["why"],
                        p["sha256"]))
    x = s["xf86-video-amdgpu"]
    pkgs.append(pkg("xf86-amdgpu", "xf86-video-amdgpu", x["tag"], x["repo"], "MIT"))
    l = s["llama.cpp"]
    pkgs.append(pkg("llama-cpp", "llama.cpp", f"{l['tag']}@{l['commit']}", l["repo"], "MIT"))
    i = s["initramfs"]
    pkgs.append(pkg("initramfs-ps4", "initramfs-ps4", i["commit"], i["repo"], "GPL-3.0-only AND BSD-3-Clause"))
    fw = json.loads((R / "manifests/firmware.json").read_text())
    for g, d in fw["groups"].items():
        pkgs.append(pkg(f"fw-{g}", f"firmware: {g}", "operator-supplied", "NOASSERTION", "NOASSERTION",
                        "NOT DISTRIBUTED by Orbis; verified against recorded hashes at build time. " + str(d.get("redistribution", ""))))
    pkgs.append(pkg("arch-snapshot", "Arch Linux Archive snapshot", s["arch_linux"]["archive_snapshot"],
                    s["arch_linux"]["url"], "NOASSERTION", "binary packages; exact list in out/rootfs/packages.txt"))
    doc = {"spdxVersion": "SPDX-2.3", "dataLicense": "CC0-1.0", "SPDXID": "SPDXRef-DOCUMENT",
           "name": f"nicklabs-ps4-orbis-linux-{orbis.__version__}",
           "documentNamespace": f"https://nicklabs.invalid/spdx/orbis-{orbis.__version__}-{hashlib.sha256(json.dumps(s).encode()).hexdigest()[:12]}",
           "creationInfo": {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                            "creators": ["Tool: orbis-sbom", "Organization: NickLabs (independent project)"]},
           "packages": pkgs,
           "relationships": [{"spdxElementId": "SPDXRef-orbis", "relationshipType": "DEPENDS_ON",
                              "relatedSpdxElement": p["SPDXID"]} for p in pkgs[1:]]}
    print(json.dumps(doc, indent=2))


if __name__ == "__main__":
    main()
