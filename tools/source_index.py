"""Print the source texts stored for one data directory.

File checks read the markdown directly. This command supplies the remaining
payload texts the evaluator already stores. It does not score a claim.
"""

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, required=True)
    args = parser.parse_args()
    path = args.data / "dataset.json"
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    sources = loaded.get("sources") if isinstance(loaded, dict) else None
    if not isinstance(sources, dict):
        print("sources missing", file=sys.stderr)
        return 1
    clean = {key: value for key, value in sources.items() if isinstance(key, str) and isinstance(value, str)}
    print(json.dumps({"sources": clean}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
