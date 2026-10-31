# Contributing

## Scope

This repository holds original source only. Do not contribute proprietary or third-party material such as vendor CAD, fonts, logos or datasheet extracts; inputs of that kind are fetched at build time and listed in `assets.lock`.

Footprint and symbol output is deterministic: rebuilds must be byte-identical, and items are written in the order KiCad saves them. A footprint already placed in a PCB must be deleted and re-created to pick up pad or net changes, so call such changes out in the pull request.

## Adding a module

See [Adding a module](README.md#adding-a-module) in the README. A new module must build with `make` and pass `make check` and `make klc` without changes to `tools/`.

## Building

See [Build](README.md#build) in the README. `cadquery-ocp` must stay on 7.9.x (see `requirements.txt`).

## Running CI checks locally

CI runs the `build` and `klc` jobs on every pull request, so running them first is optional. To catch failures before pushing:

```
make footprint symbol
make check CHECK_STEP=1
make package
make render KICAD_CLI="$PWD/tools/kicad-cli-docker.sh"
```

`make klc` needs a checkout of [kicad-library-tools](https://gitlab.com/kicad/libraries/kicad-library-tools) at the commit pinned in `.github/workflows/build.yml`:

```
make klc KLC_DIR=/path/to/kicad-library-tools/library_utils
```

`tools/klc_check.py` accepts three known errors (F7.4 on the stitching-via pads, F9.3 on the 3D model path variable, and F6.2 on the connector-kind footprints, whose origin is the socket body centre while the module envelope on F.Fab is centred elsewhere) and fails on anything else.

## Commits

Follow [Conventional Commits](https://www.conventionalcommits.org/). Sign off each commit (`git commit -s`). If an AI tool assisted, add an `Assisted-by: <model> <noreply@anthropic.com>` trailer before the `Signed-off-by` trailer.

## Pull requests

Branch from `main`. Pull requests are squash-merged, so the title follows Conventional Commits. Structure the description with the headings `### Problem`, `### Solution`, `### Regression safety` and `### Build-verified` (or `### Bench-tested` for checks done in KiCad), with a few sentences each. Both CI jobs must pass.

## Releases

Pushing a `v*` tag builds the library zip and attaches it and the renders to a GitHub Release. The workflow does not write the release body, so set the notes once the run has finished, with `gh release edit <tag> --notes-file <file>`.

Every release needs notes; do not leave the body empty. Write them for people who use the library: what was added, any breaking change with the steps to migrate, what changed in existing footprints, symbols and models (and whether a placed footprint has to be re-created), and known issues. Then check that the zip and the render files are attached.
