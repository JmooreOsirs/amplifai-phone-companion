"""Run the synthetic phone-run checks on a macOS runner without product credentials."""
import argparse
from pathlib import Path

import producer


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, required=True)
    args = parser.parse_args()
    producer.run_phone_run_checks(args.checkout.resolve(strict=True))
