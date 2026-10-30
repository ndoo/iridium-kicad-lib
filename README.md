# Iridium Modules KiCad library

Generators for a KiCad symbol, footprint and 3D model library of Iridium satellite transceiver modules. Each module has a symbol, a footprint and a 3D model in one shared library, `Iridium_Modules`.

| Module | Description | Package | Section |
| --- | --- | --- | --- |
| `Iridium_9704` | Iridium Certus 9704 L-band transceiver | 88-pad LGA, 31.5 x 42.0 mm | [Iridium 9704](#iridium-9704) |

This repository contains only original source: the generators, the module definitions and pad data, the build system and CI. It contains no built files and no third-party material. Everything built goes to `out/`, and third-party inputs are downloaded to `third_party/` at build time; both are gitignored. The GitHub Actions workflow builds the library and attaches the zip to a GitHub Release for `v*` tags.

## Using the library in KiCad

Unzip the release zip, then:

1. Preferences > Manage Symbol Libraries: add `Iridium_Modules.kicad_sym`, nickname `Iridium_Modules`.
2. Preferences > Manage Footprint Libraries: add `Iridium_Modules.pretty`, nickname `Iridium_Modules`.
3. Preferences > Configure Paths: set `IRIDIUM_MODULES_3DMODEL_DIR` to the unzipped root. Each footprint loads `${IRIDIUM_MODULES_3DMODEL_DIR}/Iridium_Modules.3dshapes/<footprint name>.step`.

Symbols are named after their footprints, for example `Iridium_Modules:Iridium_9704`.

### Migrating from the Iridium_9704 library

Earlier releases shipped a library named `Iridium_9704`. To move a design to this one:

- Remove the `Iridium_9704` symbol and footprint library entries and add the `Iridium_Modules` ones.
- Replace the `IRIDIUM_9704_3DMODEL_DIR` path variable with `IRIDIUM_MODULES_3DMODEL_DIR`.
- The footprint reference changes from `Iridium_9704:Iridium_9704` to `Iridium_Modules:Iridium_9704`. Pad names, numbers and geometry are unchanged. Update the footprint field of placed symbols and re-link the footprints on the board.

## Repository layout

| Path | Contents |
| --- | --- |
| `modules/<name>/` | One directory per module: `module.json` (names, descriptions, symbol pin table and layout, footprint parameters, expected counts, 3D model parameters), `pads.json` (pad geometry), `module.mk` (3D model build rules), and any module-specific code such as a label generator |
| `tools/` | Module-independent code: footprint and symbol generator, 3D model steps, renderer, checks, fetcher |
| `assets.lock` | Third-party inputs |
| `third_party/` | Fetched inputs (`fonts/`, `logos/`, `vendor/`); gitignored |
| `out/` | Built files; gitignored |

Everything under `third_party/` is fetched and everything under `out/` is built, so what ships in git is original source only. No directory named `assets/` exists in git; any future original artwork would go in a committed `assets/` directory, never in `third_party/` or `out/`.

## Build

Python 3.13 and GNU make are required. For the 3D models, `7z` (p7zip) or `cabextract` is also needed to unpack the Times New Roman exe.

```
pip install -r requirements.txt
make
```

`make` builds every module under `modules/`; `make MODULES=9704` builds only the named ones (the symbol library then holds only those). `make footprint symbol` needs no downloads. `make fetch` downloads the inputs; `make step` and `make package` fetch them as needed. `make render` draws each module's footprint, symbol and 3D model with `kicad-cli pcb render`, the same approach as the kicad-render-pcb GitHub Action but on KiCad 10. It needs `kicad-cli` and `rsvg-convert`; `KICAD_CLI` selects the command, and `tools/kicad-cli-docker.sh` runs it from the official KiCad Docker image, as CI does. Other targets: `label`, `can`, `check`, `klc`, `clean` (removes `out/`), `distclean` (also removes `third_party/`). Variables `PYTHON`, `OUT` and `THIRD_PARTY` can be overridden.

Output layout:

| Path | Contents |
| --- | --- |
| `out/Iridium_Modules.kicad_sym` | Symbol library, one symbol per module |
| `out/Iridium_Modules.pretty/<name>.kicad_mod` | Footprint, one file per module |
| `out/Iridium_Modules.3dshapes/<name>.step` | 3D model, one file per module |
| `out/build/<module>/` | Intermediates for a module's 3D model |
| `out/Iridium_Modules-kicad-lib.zip` | `make package`: the three libraries and `INSTALL.txt` |
| `out/render/<module>/` | `make render`: `render-top.png`, `render-bottom.png`, `render-angled.png`, `render-rotating.gif`, `render-footprint.png`, `render-symbol.png` |

Footprint UUIDs are derived from fixed keys, so rebuilds of footprints and symbols are byte-identical.

## Adding a module

1. Create `modules/<name>/` and add `module.json` and `pads.json`, following `modules/9704/`. The module's name in `module.json` must be `Iridium_<name>`; it becomes the symbol name and the footprint and STEP file name. Give it its own `uuid_namespace`.
2. In `module.json`, list the pad geometry file, alignment holes, stitching via pads, the expected pad, via and pin counts, and the symbol's power, ground and signal pins and layout. `make check` and the generators fail if the built counts differ from the expected ones.
3. For a 3D model, add a `model` section and a `modules/<name>/module.mk` that adds any intermediate targets to `LABELS` and `CANS` and defines the rule for `$(OUT)/$(LIB).3dshapes/Iridium_<name>.step`. `modules/9704/module.mk` shows a model built from a vendor STEP and a generated label.
4. Add the module to the table above and give it a section below, using the same subheadings as the existing ones.
5. Add any third-party inputs to `assets.lock`.

## Testing and CI

There are no unit tests. Changes are checked by the GitHub Actions workflow, which runs on every pull request and on pushes to `main`:

- `build` runs `make package`, `make check CHECK_STEP=1` (pad and pin counts against each `module.json`, non-empty STEP) and `make render`, and uploads the library zip and the renders.
- `klc` runs the KiCad Library Convention checks on the footprints and symbols with `make klc`.
- `pages` publishes the renders to GitHub Pages on pushes to `main`.
- `release` attaches the zip and renders to a GitHub Release for `v*` tags.

See [CONTRIBUTING.md](CONTRIBUTING.md) for running the same checks locally.

## Third-party inputs

`tools/fetch.py` downloads the inputs listed in `assets.lock`, checking each download and each extracted file against its sha256, trying the listed mirrors in order.

| Entry | Used by | Source | Licence note |
| --- | --- | --- | --- |
| `vendor-step` | 9704 | Ground Control RockBLOCK 9704 STEP zip | Vendor CAD; used to derive the shield can geometry |
| `iridium-logo` | 9704 | Wikimedia Commons | Trademark of Iridium Satellite LLC |
| `ce-logo` | 9704 | Wikimedia Commons | CE marking; URL is not revision-pinned, the checksum is the pin |
| `myriadpro-*` | 9704 | Public GitHub repo pinned to a commit, plus jsDelivr and githack copies of it (Regular also from a second repo) | Adobe commercial font; fetched, never committed or re-hosted |
| `timesbd` | 9704 | `times32.exe` from the SourceForge corefonts project, plus raw GitHub and CDN copies of the same file | Microsoft core fonts EULA: redistribution of the unmodified original only |

The release zip contains content derived from these inputs; responsibility for distributing it rests with whoever publishes it. `FETCH_UA` sets the download User-Agent, and `FETCH_MIRROR_FIRST=<substring>` tries a matching mirror first. Every mirror must serve bytes matching its recorded sha256; `make verify` checks the fetched files and `python tools/fetch.py --check-mirrors` downloads and checks every mirror.

## Iridium 9704

The Iridium Certus 9704 L-band transceiver module: 88-pad LGA, 31.5 x 42.0 mm. Datasheet and product page: <https://www.iridium.com/products/iridium-certus-9704/>. Definition: `modules/9704/`.

<table>
  <tr>
    <td colspan="6" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-angled.png"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-angled.png" alt="Angled view of the 3D model" width="800"></a></td>
  </tr>
  <tr>
    <td colspan="2" width="33%" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-top.png"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-top.png" alt="Top view of the 3D model" width="280"></a><br><sub>Top</sub></td>
    <td colspan="2" width="33%" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-bottom.png"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-bottom.png" alt="Bottom view of the 3D model" width="280"></a><br><sub>Bottom</sub></td>
    <td colspan="2" width="33%" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-rotating.gif"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-rotating.gif" alt="Rotating view of the 3D model" width="280"></a><br><sub>Rotating</sub></td>
  </tr>
  <tr>
    <td colspan="3" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-footprint.png"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-footprint.png" alt="Footprint" width="400"></a><br><sub><b>Footprint</b></sub></td>
    <td colspan="3" align="center"><a href="https://ndoo.github.io/iridium-kicad-lib/9704/render-symbol.png"><img src="https://ndoo.github.io/iridium-kicad-lib/9704/render-symbol.png" alt="Symbol" width="400"></a><br><sub><b>Symbol</b></sub></td>
  </tr>
</table>

The images are built from `main` by the workflow and published to GitHub Pages. They show the label artwork, so they are not committed.

### Footprint

88 SMD pads from `modules/9704/pads.json`, ground stitching vias on ten ground pads (20 vias), and the module's two alignment holes drawn on `Dwgs.User`. The footprint is turned a quarter turn from the pad data's orientation.

### Symbol

Signal pins are listed in `modules/9704/module.json`. Ground, `V_BAT_MAIN` and `V_BAT_PA` are native KiCad pin stacks, and the 30 unconnected pins are hidden inside the body, so the symbol file has 52 pin entries for 88 pins.

### 3D model

The model is built from three parts:

- `modules/9704/label.py` builds the label artwork as SVG from the fetched fonts and logos.
- `tools/model.py can` extracts the shield can geometry from the vendor STEP.
- `tools/model.py assemble` combines a PCB box, the can and the label into the module STEP.

The build rules are in `modules/9704/module.mk`.

## License

The source in this repository is released under the [MIT License](LICENSE). The built release zip also contains content derived from the third-party inputs above, which the MIT License does not cover.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Coding agents should also read [AGENTS.md](AGENTS.md).
