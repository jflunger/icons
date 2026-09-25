"""Render random icons per set through the SVG endpoint and parse them as XML.

Usage: .venv/bin/python scripts/check_svgs.py [--per-set 50] [--base http://localhost:9005]
Without --base it uses an in-process TestClient against data/icons.db.
"""
import argparse
import random
import sqlite3
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-set", type=int, default=50)
    ap.add_argument("--base", help="server URL; default is in-process")
    args = ap.parse_args()

    conn = sqlite3.connect(ROOT / "data" / "icons.db")
    by_set: dict[str, list[str]] = {}
    for prefix, name in conn.execute("SELECT prefix, name FROM icons"):
        by_set.setdefault(prefix, []).append(name)

    if args.base:
        import httpx
        client = httpx.Client(base_url=args.base, timeout=30)
    else:
        import os
        os.environ["ICONSEARCH_NO_SCHEDULER"] = "1"
        from fastapi.testclient import TestClient
        from iconsearch.server import app
        client = TestClient(app)

    failures = 0
    for prefix, names in sorted(by_set.items()):
        sample = random.sample(names, min(args.per_set, len(names)))
        bad = []
        for name in sample:
            r = client.get(f"/icon/{prefix}/{name}.svg", params={"color": "2563eb", "size": 256, "stroke": 1.5})
            try:
                assert r.status_code == 200, r.status_code
                root = ET.fromstring(r.text)
                assert root.get("width") and root.get("height")
                assert "currentColor" not in r.text
            except Exception as e:  # noqa: BLE001
                bad.append(f"{name}: {e}")
        failures += len(bad)
        print(f"{prefix:<10} {len(sample) - len(bad):>3}/{len(sample)} ok" + (f"  FAIL {bad[:3]}" if bad else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
