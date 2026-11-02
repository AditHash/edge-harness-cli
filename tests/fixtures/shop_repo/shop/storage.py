import json
from pathlib import Path


class JsonStorage:
    """Persist orders as one JSON file per order id."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def save(self, order_id: str, payload: dict) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        (self.directory / f"{order_id}.json").write_text(json.dumps(payload))

    def load(self, order_id: str) -> dict:
        return json.loads((self.directory / f"{order_id}.json").read_text())
