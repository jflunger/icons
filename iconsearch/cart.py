"""Icon shopping cart: a list of {purpose, icon} entries kept in data/cart.json.

The web UI edits it behind a password; two local-only endpoints let scripts on
this server post a shopping list (purposes without icons) and read the filled cart.
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from pathlib import Path

MAX_ITEMS = 500
MAX_PURPOSE = 300
# The icon fields the frontend needs to draw an entry without another lookup.
ICON_KEYS = ("id", "prefix", "name", "width", "height", "body", "is_stroke")


class Cart:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _read(self) -> list[dict]:
        try:
            return json.loads(self.path.read_text())["items"]
        except (FileNotFoundError, KeyError, ValueError):
            return []

    def _write(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"items": items}, indent=1))
        os.replace(tmp, self.path)

    def items(self) -> list[dict]:
        with self._lock:
            return self._read()

    def _edit(self, fn) -> list[dict]:
        with self._lock:
            items = self._read()
            fn(items)
            self._write(items)
            return items

    @staticmethod
    def _entry(purpose: str, icon: dict | None) -> dict:
        return {"id": uuid.uuid4().hex[:12], "purpose": purpose[:MAX_PURPOSE], "icon": icon}

    def add(self, purpose: str, icon: dict | None) -> list[dict]:
        def fn(items):
            if len(items) >= MAX_ITEMS:
                raise ValueError(f"the cart holds up to {MAX_ITEMS} entries")
            items.append(self._entry(purpose, icon))
        return self._edit(fn)

    def add_purposes(self, purposes: list[str]) -> list[dict]:
        """Append icon-less entries, skipping purposes the cart already has."""
        def fn(items):
            have = {i["purpose"] for i in items}
            for p in purposes:
                p = p.strip()[:MAX_PURPOSE]
                if p and p not in have:
                    if len(items) >= MAX_ITEMS:
                        raise ValueError(f"the cart holds up to {MAX_ITEMS} entries")
                    items.append(self._entry(p, None))
                    have.add(p)
        return self._edit(fn)

    def update(self, entry_id: str, *, purpose: str | None = None, icon: dict | None = None) -> list[dict]:
        def fn(items):
            for i in items:
                if i["id"] == entry_id:
                    if purpose is not None:
                        i["purpose"] = purpose[:MAX_PURPOSE]
                    if icon is not None:
                        i["icon"] = icon
                    return
            raise KeyError(entry_id)
        return self._edit(fn)

    def remove(self, entry_id: str) -> list[dict]:
        return self._edit(lambda items: items.__setitem__(slice(None), [i for i in items if i["id"] != entry_id]))

    def clear(self) -> list[dict]:
        return self._edit(lambda items: items.clear())
