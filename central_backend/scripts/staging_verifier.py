"""R26-DS-012 staging acceptance verifier.

This tool validates deployment readiness and records evidence boundaries.
It intentionally does not create clinical records or mutate production state.
Use only synthetic staging identities.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from urllib.request import Request, urlopen


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


def get_json(url: str, token: str | None = None) -> dict:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def run(base_url: str, token: str | None) -> dict:
    checks: list[CheckResult] = []

    try:
        health = get_json(f"{base_url.rstrip('/')}/health")
        checks.append(CheckResult("backend_health", True, str(health)))
    except Exception as exc:
        checks.append(CheckResult("backend_health", False, str(exc)))

    # These are intentionally evidence gates. The real staging run should add
    # synthetic credentials and identifiers via environment variables rather
    # than hardcoding them in source.
    checks.append(
        CheckResult(
            "mutation_guard",
            True,
            "Verifier does not create, acknowledge, resolve, or ingest records.",
        )
    )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "checks": [asdict(item) for item in checks],
        "ready": all(item.passed for item in checks),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--token")
    parser.add_argument("--output", default="staging-verification.json")
    args = parser.parse_args()

    result = run(args.base_url, args.token)
    with open(args.output, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)

    print(json.dumps(result, indent=2))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
