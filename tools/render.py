#!/usr/bin/env python3
"""Render each module's footprint, symbol and 3D model to PNG/GIF images with kicad-cli.

Writes a scratch board holding the footprint, then renders it the way the
kicad-render-pcb GitHub Action does (top, bottom, plus an angled view and a
rotating animation) and exports 2D images of the footprint and the symbol.

Usage: python tools/render.py --module modules/9704 [--module ...] [--out out/render] [--lib out]
Images for a module are written to <out>/<module directory name>/.
Environment: KICAD_CLI is the kicad-cli command (default: kicad-cli); RSVG is
rsvg-convert (default: rsvg-convert). Each image gets a progress line; command output
is shown only on failure, and RENDER_VERBOSE=1 echoes every command and its output.
"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid

from PIL import Image, ImageChops

from kicad import LIB, load_module

BOARD_CENTRE = (60.0, 50.0)
BOARD_MARGIN = 5.0
FRAME_STEP = 3       # degrees per rotation frame
FRAME_SIZE = 400     # pixels
FRAME_ZOOM = 0.7
TURN_MS = 4800       # one full turn; GIF delays are in 10 ms units

BOARD_HEADER = """(kicad_pcb
\t(version 20260206)
\t(generator "pcbnew")
\t(generator_version "10.0")
\t(general
\t\t(thickness 1.6)
\t\t(legacy_teardrops no)
\t)
\t(paper "A4")
\t(layers
\t\t(0 "F.Cu" signal)
\t\t(2 "B.Cu" signal)
\t\t(9 "F.Adhes" user "F.Adhesive")
\t\t(11 "B.Adhes" user "B.Adhesive")
\t\t(13 "F.Paste" user)
\t\t(15 "B.Paste" user)
\t\t(5 "F.SilkS" user "F.Silkscreen")
\t\t(7 "B.SilkS" user "B.Silkscreen")
\t\t(1 "F.Mask" user)
\t\t(3 "B.Mask" user)
\t\t(17 "Dwgs.User" user "User.Drawings")
\t\t(19 "Cmts.User" user "User.Comments")
\t\t(21 "Eco1.User" user "User.Eco1")
\t\t(23 "Eco2.User" user "User.Eco2")
\t\t(25 "Edge.Cuts" user)
\t\t(27 "Margin" user)
\t\t(31 "F.CrtYd" user "F.Courtyard")
\t\t(29 "B.CrtYd" user "B.Courtyard")
\t\t(35 "F.Fab" user)
\t\t(33 "B.Fab" user)
\t)
\t(setup
\t\t(pad_to_mask_clearance 0)
\t\t(tenting (front yes) (back yes))
\t)
\t(net 0 "")
"""


def board_text(footprint_path, module):
    """A board with the footprint at BOARD_CENTRE inside a rectangular outline."""
    cx, cy = BOARD_CENTRE
    # The footprint is turned a quarter, so width and height swap
    hw = module["pad_data"]["mod_h"] / 2 + BOARD_MARGIN
    hh = module["pad_data"]["mod_w"] / 2 + BOARD_MARGIN
    outline_uuid = uuid.uuid5(uuid.UUID(module["uuid_namespace"]), "render:outline")
    with open(footprint_path) as f:
        lines = f.read().rstrip("\n").split("\n")
    placed = []
    for line in lines:
        placed.append(line)
        if line == '\t(layer "F.Cu")':
            placed.append(f"\t(at {cx} {cy})")
    outline = (f'\t(gr_rect (start {cx - hw} {cy - hh}) (end {cx + hw} {cy + hh}) '
               f'(stroke (width 0.05) (type default)) (fill no) (layer "Edge.Cuts") '
               f'(uuid "{outline_uuid}"))')
    return BOARD_HEADER + "\n".join(placed).replace("\n(footprint", "\n\t(footprint", 1) + "\n" + outline + "\n)\n"


def pad_svg(path, margin):
    """Grow a kicad-cli SVG's viewBox by margin (mm) on every side; the export crops
    to the footprint and clips text and markers that stick out."""
    with open(path) as f:
        text = f.read()
    m = re.search(r'width="([\d.]+)mm"\s+height="([\d.]+)mm"\s+viewBox="([-\d.]+) ([-\d.]+) ([\d.]+) ([\d.]+)"', text)
    w, h, x, y, vw, vh = (float(v) for v in m.groups())
    new = (f'width="{w + 2 * margin:.6f}mm" height="{h + 2 * margin:.6f}mm" '
           f'viewBox="{x - margin:.6f} {y - margin:.6f} {vw + 2 * margin:.6f} {vh + 2 * margin:.6f}"')
    with open(path, "w") as f:
        f.write(text[:m.start()] + new + text[m.end():])


def fit(path, aspect=None, transparent=True, margin=0.04):
    """Crop a PNG to its content plus a margin, then pad it to width/height = aspect."""
    img = Image.open(path).convert("RGBA")
    if transparent:
        bbox = img.getchannel("A").getbbox()
        fill = (0, 0, 0, 0)
    else:
        bbox = ImageChops.difference(img.convert("RGB"), Image.new("RGB", img.size, "white")).getbbox()
        fill = (255, 255, 255, 255)
    if bbox is None:
        sys.exit(f"render: {os.path.basename(path)} is blank")
    img = img.crop(bbox)
    pad = round(max(img.size) * margin)
    w, h = img.width + 2 * pad, img.height + 2 * pad
    if aspect:
        w, h = max(w, round(h * aspect)), max(h, round(w / aspect))
    out = Image.new("RGBA", (w, h), fill)
    out.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    out.save(path, optimize=True)


def gif_frame(img):
    """Palette frame with index 255 transparent; edges are matted with mid-grey."""
    matte = Image.new("RGBA", img.size, (128, 128, 128, 255))
    matte.alpha_composite(img)
    frame = matte.convert("RGB").quantize(255)
    frame.paste(255, mask=img.getchannel("A").point(lambda a: 255 if a < 128 else 0))
    return frame


def step(msg):
    print("render:", msg, flush=True)


def run(cmd, env=None, stream=False):
    verbose = bool(os.environ.get("RENDER_VERBOSE"))
    if verbose:
        print("+", " ".join(shlex.quote(c) for c in cmd), flush=True)
    res = subprocess.run(cmd, env=env, capture_output=not (verbose or stream), text=True)
    if res.returncode:
        sys.stderr.write((res.stdout or "") + (res.stderr or ""))
        sys.exit(f"render: failed: {' '.join(shlex.quote(c) for c in cmd)}")


def render_module(cli, rsvg, lib, out, module, env):
    module_name = module["name"]
    os.makedirs(out, exist_ok=True)
    footprint = os.path.join(lib, f"{LIB}.pretty", f"{module_name}.kicad_mod")
    symbols = os.path.join(lib, f"{LIB}.kicad_sym")
    # Scratch files live under the output directory so a containerised kicad-cli, which
    # mounts only the working directory, can read them.
    with tempfile.TemporaryDirectory(prefix=".render-", dir=out) as tmp:
        board = os.path.join(tmp, "board.kicad_pcb")
        with open(board, "w") as f:
            f.write(board_text(footprint, module))

        def render(name, *extra):
            step(name)
            run(cli + ["pcb", "render", "--background", "transparent", *extra,
                       "-o", os.path.join(out, name), board], env=env)

        # Hero 2:1, supporting views square, so each README row has uniform cell shapes
        render("render-angled.png", "--rotate", "-50,0,25", "--perspective", "--zoom", "0.8",
               "--width", "2400", "--height", "1350")
        fit(os.path.join(out, "render-angled.png"), aspect=2)
        for name, side in (("render-top.png", "top"), ("render-bottom.png", "bottom")):
            render(name, "--side", side, "--width", "1600", "--height", "1200")
            fit(os.path.join(out, name), aspect=1)

        angles = range(0, 360, FRAME_STEP)
        step(f"render-rotating.gif ({len(angles)} frames)")
        frames_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "render-frames.sh")
        frames_env = dict(env)
        if os.path.basename(cli[0]) == "kicad-cli-docker.sh":
            frames_env["KICAD_DOCKER_CMD"] = "sh"
            frames_cmd = cli + [frames_script]
        else:
            frames_cmd = ["sh", frames_script]
        frames_cmd += [tmp, str(FRAME_STEP), str(FRAME_SIZE), str(FRAME_ZOOM)]
        run(frames_cmd, env=frames_env, stream=True)
        frames = [gif_frame(Image.open(os.path.join(tmp, f"frame{i:03d}.png")).convert("RGBA"))
                  for i in range(len(angles))]
        frames[0].save(os.path.join(out, "render-rotating.gif"), save_all=True,
                       append_images=frames[1:], duration=round(TURN_MS / len(angles) / 10) * 10, loop=0, optimize=False,
                       transparency=255, disposal=2)

        step("render-footprint.png")
        run(cli + ["fp", "export", "svg", "--footprint", module_name, "--layers",
                   "F.Cu,F.Mask,F.SilkS,F.Fab,F.CrtYd,Dwgs.User", "-o", tmp,
                   os.path.join(lib, f"{LIB}.pretty")], env=env)
        pad_svg(os.path.join(tmp, f"{module_name}.svg"), 2.5)
        run([rsvg, "-w", "1600", "-b", "white", os.path.join(tmp, f"{module_name}.svg"),
             "-o", os.path.join(out, "render-footprint.png")])
        fit(os.path.join(out, "render-footprint.png"), aspect=4 / 3, transparent=False)
        step("render-symbol.png")
        run(cli + ["sym", "export", "svg", "--symbol", module_name, "-o", tmp, symbols], env=env)
        run([rsvg, "-w", "1200", "-b", "white", os.path.join(tmp, f"{module_name}_unit1.svg"),
             "-o", os.path.join(out, "render-symbol.png")])
        fit(os.path.join(out, "render-symbol.png"), aspect=4 / 3, transparent=False)
    for image in sorted(os.listdir(out)):
        print(f"{os.path.getsize(os.path.join(out, image)):>9}  {image}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lib", default="out", help="directory with the built library")
    ap.add_argument("--out", default="out/render")
    ap.add_argument("--module", action="append", required=True)
    args = ap.parse_args()

    cli = shlex.split(os.environ.get("KICAD_CLI", "kicad-cli"))
    rsvg = os.environ.get("RSVG", "rsvg-convert")
    lib = os.path.abspath(args.lib)
    env = dict(os.environ, IRIDIUM_MODULES_3DMODEL_DIR=lib)
    for path in args.module:
        out = os.path.join(os.path.abspath(args.out), os.path.basename(os.path.normpath(path)))
        render_module(cli, rsvg, lib, out, load_module(path), env)


if __name__ == "__main__":
    sys.exit(main())
