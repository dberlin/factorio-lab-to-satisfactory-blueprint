"""Re-download the Satisfactory blueprint fixtures listed in MANIFEST.json.

Download URLs are https://satisfactoryblueprints.com/blueprint/download/<uuid>
(.sbp) and /blueprint/config/<uuid> (.sbpcfg). Existing files are kept unless
--force is given.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "sfy"
BASE = "https://satisfactoryblueprints.com/blueprint"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    manifest = json.loads((FIXTURES / "MANIFEST.json").read_text())
    with httpx.Client(follow_redirects=True, timeout=60) as client:
        for entry in manifest["entries"]:
            for key, kind in (("sbp", "download"), ("sbpcfg", "config")):
                target = FIXTURES / entry[key]
                if target.exists() and not args.force:
                    continue
                response = client.get(f"{BASE}/{kind}/{entry['uuid']}")
                response.raise_for_status()
                target.write_bytes(response.content)
                print(f"wrote {target.name} ({len(response.content)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
