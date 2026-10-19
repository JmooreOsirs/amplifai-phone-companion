#!/bin/sh
# Build a fresh FAKE DATA DEMO bundle under /private/tmp, check its isolation, then open it.
# The app re-enters its own no-network sandbox however it is opened, and never uses a real iPhone or account.
set -eu
source_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
output="/private/tmp/amplifai-fake-demo.$(date +%Y%m%d-%H%M%S)"
python3 -I "$source_root/script/build_fake_data_demo.py" --output "$output" > /dev/null
echo "Isolation receipt: $output/fake-demo-isolation.json"
echo "Demo app (double-click to reopen until the next restart): $output/AMPLIFai FAKE DATA DEMO.app"
open -n "$output/AMPLIFai FAKE DATA DEMO.app"
