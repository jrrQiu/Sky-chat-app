from __future__ import annotations

import json

from eval.metrics import evaluate_retrieval


def main() -> None:
    report = evaluate_retrieval()
    print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
