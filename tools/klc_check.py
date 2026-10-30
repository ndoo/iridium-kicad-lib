#!/usr/bin/env python3
"""Run the KLC footprint and symbol checks, tolerating two known footprint errors.

Needs a checkout of kicad-library-tools; pass its library_utils directory as --klc.
Any error other than the exceptions in expected() fails the run.
"""

import argparse
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

from kicad import LIB, load_module


def expected(rule, entry, module):
    if rule == "F7.4":
        # The stitching vias have no mask layers, so they are tented
        extras = {f"Pad '{n}' missing layer '*.Mask'" for n in module["footprint"]["via_pins"]}
        return (entry.message == "Some THT pads have incorrect layer settings"
                and bool(entry.extras) and set(entry.extras) <= extras)
    if rule == "F9.3":
        # The model is referenced through a user-defined path variable
        directory = f"{LIB}.3dshapes"
        return (entry.message == (
                    f"3D model directory is different from footprint directory (found "
                    f"'${{IRIDIUM_MODULES_3DMODEL_DIR}}/{directory}', should be '{directory}')")
                and set(entry.extras) == {
                    f"3D model path: ${{IRIDIUM_MODULES_3DMODEL_DIR}}/{directory}/{module['name']}.step"})
    return False


def check_footprints(klc, files, modules_dir):
    sys.path[:0] = [str(klc / "klc-check"), str(klc / "common")]
    from kicad_mod import KicadMod
    from rulebase import Severity
    from rules_footprint import get_all_footprint_rules

    failures = 0
    for path in files:
        kicad_mod = KicadMod(str(path))
        module = load_module(str(modules_dir / path.stem.removeprefix("Iridium_")))
        for name, mod in get_all_footprint_rules().items():
            rule = mod.Rule(kicad_mod, SimpleNamespace())
            rule.check()
            for entry in rule.logEntries:
                if entry.severity != Severity.ERROR:
                    continue
                if expected(name, entry, module):
                    print(f"{path.name}: {name}: expected: {entry.message}")
                    continue
                failures += 1
                print(f"{path.name}: {name}: {entry.message}")
                for extra in entry.extras:
                    print(f"    {extra}")
    return failures


def check_symbols(klc, files, footprint_dir):
    cmd = [sys.executable, str(klc / "klc-check" / "check_symbol.py"), "--nocolor", "-w", "-vv"]
    cmd += ["--footprints", str(footprint_dir)]
    return subprocess.run(cmd + [str(f) for f in files]).returncode != 0


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--klc", type=Path, required=True, help="kicad-library-tools/library_utils directory")
    ap.add_argument("--lib", type=Path, default=Path("out"))
    ap.add_argument("--modules", type=Path, default=Path("modules"), help="directory holding the module definitions")
    args = ap.parse_args()

    footprints = sorted(args.lib.glob("*.pretty/*.kicad_mod"))
    symbols = sorted(args.lib.glob("*.kicad_sym"))
    if not footprints or not symbols:
        sys.exit(f"no footprints or symbols under {args.lib}")

    failed = check_footprints(args.klc, footprints, args.modules) > 0
    failed |= check_symbols(args.klc, symbols, args.lib)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
