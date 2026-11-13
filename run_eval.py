"""Call the local API for each labeled case and report category accuracy."""

import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

CASES_PATH = Path(__file__).resolve().parent / "evals" / "cases.json"
ENDPOINT = "http://localhost:8000/triage"


def main() -> None:
    cases = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    correct = 0
    failures: list[str] = []
    for case in cases:
        # Send each hand-labeled example through the same HTTP endpoint as a real request.
        request = Request(
            ENDPOINT,
            data=json.dumps({"text": case["text"]}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=40) as response:
                output = json.loads(response.read().decode("utf-8"))
        except (HTTPError, URLError, TimeoutError) as exc:
            failures.append(f"{case['id']}: request failed ({exc})")
            continue
        if output.get("category") == case["expected"]["category"]:
            # Category is the evaluation's primary field; urgency remains visible in each case.
            correct += 1
        else:
            failures.append(
                f"{case['id']}: expected {case['expected']['category']}, got {output.get('category')}"
            )

    print(f"category_accuracy={correct}/{len(cases)} ({correct / len(cases):.0%})")
    for failure in failures:
        print(f"FAILED {failure}")


if __name__ == "__main__":
    main()
