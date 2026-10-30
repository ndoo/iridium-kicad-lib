#!/usr/bin/env python3
"""Check the built library against the expectations in each module.json.

Usage: python tools/check.py --lib out [--step] --module modules/9704 [--module ...]
"""

import argparse
import os
import re
import sys

from kicad import LIB, load_module


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", default="out")
    ap.add_argument("--step", action="store_true", help="require each STEP model to exist")
    ap.add_argument("--module", action="append", required=True)
    args = ap.parse_args()

    symbol_path = os.path.join(args.lib, f"{LIB}.kicad_sym")
    if not os.path.getsize(symbol_path):
        sys.exit(f"missing or empty {symbol_path}")
    with open(symbol_path) as f:
        symbol_blocks = re.split(r'(?m)^\t\(symbol "', f.read())[1:]
    symbols = {b.split('"', 1)[0]: b for b in symbol_blocks}

    failures = []
    for path in args.module:
        module = load_module(path)
        name = module["name"]

        footprint_path = os.path.join(args.lib, f"{LIB}.pretty", f"{name}.kicad_mod")
        if not os.path.exists(footprint_path) or not os.path.getsize(footprint_path):
            failures.append(f"missing or empty {footprint_path}")
        else:
            with open(footprint_path) as f:
                pads = len(re.findall(r'\(pad "[0-9]+" smd', f.read()))
            want = module["footprint"]["expect"]["smd"]
            if pads != want:
                failures.append(f"{name}: expected {want} numbered pads, got {pads}")

        block = symbols.get(name)
        if block is None:
            failures.append(f"{name}: symbol missing from {symbol_path}")
        else:
            entries = block.count("(pin ")
            want = module["symbol"]["expect_pin_entries"]
            if entries != want:
                failures.append(f"{name}: expected {want} symbol pin entries, got {entries}")

        step_path = os.path.join(args.lib, f"{LIB}.3dshapes", f"{name}.step")
        if os.path.exists(step_path):
            if not os.path.getsize(step_path):
                failures.append(f"{name}: empty {step_path}")
        elif args.step:
            failures.append(f"{name}: missing {step_path}")
        else:
            print(f"{name}: STEP not built, skipped")

    if failures:
        sys.exit("\n".join(failures))
    print("check OK")


if __name__ == "__main__":
    main()
