#!/usr/bin/env python3
"""Generate the KiCad footprints and symbols for the Iridium modules.

Each module lives in modules/<name>/ (module.json plus its pad file).

  footprint  .kicad_mod for one module, following the Astrocast_AST50147-00
             footprint conventions
  symbol     one .kicad_sym holding a symbol per module, following the
             AST50147-xx KLC pattern from the kicad-symbols library

Usage:
  python tools/kicad.py footprint --module modules/9704 [--variant NAME] --out Iridium_Modules.pretty/Iridium_9704.kicad_mod
  python tools/kicad.py symbol --module modules/9704 [--module ...] --out Iridium_Modules.kicad_sym
"""

import argparse
import json
import math
import os
import re
import uuid as _uuid

LIB = "Iridium_Modules"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_module(path):
    """Read modules/<name>/module.json and attach its pad data as "pad_data".

    The footprint comes from exactly one of footprint.pads (a pad file), reuse.pads (another
    module's pad data) and reuse.footprint (derived from a fetched upstream footprint, which
    has no pad data here).
    """
    with open(os.path.join(path, "module.json")) as f:
        module = json.load(f)
    expected = "Iridium_" + os.path.basename(os.path.normpath(path))
    if module["name"] != expected:
        raise SystemExit(f"{path}: name {module['name']!r} must be {expected!r}")
    module["dir"] = path
    reuse = module.get("reuse", {})
    sources = [name for name, present in (("footprint.pads", "pads" in module["footprint"]),
                                          ("reuse.pads", "pads" in reuse),
                                          ("reuse.footprint", "footprint" in reuse)) if present]
    if len(sources) != 1:
        raise SystemExit(f"{path}: needs exactly one of footprint.pads, reuse.pads and "
                         f"reuse.footprint, found {', '.join(sources) or 'none'}")
    if "pads" in module["footprint"]:
        with open(os.path.join(path, module["footprint"]["pads"])) as f:
            module["pad_data"] = json.load(f)
    elif "pads" in reuse:
        other = load_module(os.path.join(os.path.dirname(os.path.normpath(path)), reuse["pads"]["module"]))
        if "pad_data" not in other:
            raise SystemExit(f"{path}: reuse.pads.module {reuse['pads']['module']!r} has no pad data")
        module["pad_data"] = other["pad_data"]
    return module


def upstream_path(name):
    """Path of the fetched assets.lock entry `name` under the third_party directory."""
    with open(os.path.join(ROOT, "assets.lock")) as f:
        entries = {e["name"]: e for e in json.load(f)["entries"]}
    if name not in entries:
        raise SystemExit(f"assets.lock has no entry {name!r}")
    third_party = os.environ.get("THIRD_PARTY") or os.path.join(ROOT, "third_party")
    path = os.path.join(third_party, entries[name]["dest"])
    if not os.path.isfile(path):
        raise SystemExit(f"{path} is missing; run: python tools/fetch.py {name}")
    return path


def board_bounds(module):
    """Corners of the module's footprint as placed on the board, relative to the footprint origin."""
    if module["footprint"].get("kind") == "connector":
        return module["footprint"]["envelope"]
    if "footprint" in module.get("reuse", {}):
        width, height = module["reuse"]["footprint"]["body"]
    else:
        # The generator turns its pad data a quarter
        width, height = module["pad_data"]["mod_h"], module["pad_data"]["mod_w"]
    return [-width / 2, -height / 2], [width / 2, height / 2]


def _write(path, content):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)


# ---------------------------------------------------------------------------
# Footprint
# ---------------------------------------------------------------------------

def _make_u(namespace):
    """Return a UUID generator; ids are uuid5(namespace, key) so output is reproducible."""
    n = [0]

    def _u(key=None):
        if key is None:
            n[0] += 1
            key = f"item:{n[0]}"
        return str(_uuid.uuid5(namespace, key))
    return _u


def _n(v, digits=6):
    """Format a number the way KiCad writes it: no trailing zeros, no "-0"."""
    t = f"{v:.{digits}f}".rstrip("0").rstrip(".")
    return "0" if t in ("", "-0") else t


# KiCad's layer order, which sorts the graphic items within a footprint
_LAYER_ORDER = ["F.Cu", "B.Cu", "B.Adhes", "F.Adhes", "B.Paste", "F.Paste",
                "B.SilkS", "F.SilkS", "B.Mask", "F.Mask", "Dwgs.User",
                "Cmts.User", "Eco1.User", "Eco2.User", "Edge.Cuts", "Margin",
                "B.CrtYd", "F.CrtYd", "B.Fab", "F.Fab"]
_SHAPE_ORDER = ["fp_line", "fp_rect", "fp_arc", "fp_circle", "fp_poly"]
_FP_HEAD = ("version", "generator", "generator_version", "layer", "descr",
            "tags", "property", "attr", "duplicate_pad_numbers_are_jumpers")


def _nm(value):
    return round(float(value) * 1e6)


def _kind(block):
    return block[0].strip()[1:].split()[0]


def _fp_sort_key(block):
    """Sort key placing a footprint item where KiCad's serializer would."""
    text = "\n".join(block)
    kind = _kind(block)

    def nums(tag):
        m = re.search(rf"\({tag} ([^)]*)\)", text)
        return tuple(_nm(v) for v in m.group(1).split()) if m else ()

    if kind == "pad":
        number = re.match(r'\t\(pad "([^"]*)"', block[0]).group(1)
        return (2, int(number) if number.isdigit() else -1, nums("at"), nums("size"))
    layer = _LAYER_ORDER.index(re.search(r'\(layer "([^"]+)"\)', text).group(1))
    if kind == "fp_text":
        return (1, layer, 0, nums("at"))
    pts = tuple(_nm(v) for v in re.findall(r"\(xy ([^)]*)\)", text)
                for v in v.split()) if kind == "fp_poly" else ()
    return (0, layer, _SHAPE_ORDER.index(kind),
            nums("start") or nums("center"), nums("end"), pts)


def _fp_sort(lines):
    """Order the items of a footprint the same way KiCad saves them."""
    blocks = []
    for line in lines[1:-1]:
        if line.startswith("\t(") and not line.startswith("\t\t"):
            blocks.append([])
        blocks[-1].append(line)
    head = [b for b in blocks if _kind(b) in _FP_HEAD]
    body = sorted((b for b in blocks if _kind(b) in _SHAPE_ORDER + ["fp_text", "pad"]),
                  key=_fp_sort_key)
    tail = [b for b in blocks if b not in head and b not in body]
    return [lines[0]] + [l for b in head + body + tail for l in b] + [lines[-1]]


def derive_footprint(module):
    """Return the upstream footprint named by reuse.footprint, renamed for this module.

    The name, description, tags, Value text, 3D model path and ids change; the rest is copied.
    """
    with open(upstream_path(module["reuse"]["footprint"]["upstream"])) as f:
        text = f.read()
    name = module["name"]
    model = f"${{IRIDIUM_MODULES_3DMODEL_DIR}}/{LIB}.3dshapes/{name}.step"

    def replace_once(pattern, repl):
        nonlocal text
        text, count = re.subn(pattern, repl, text, count=1)
        if count != 1:
            raise SystemExit(f"upstream footprint has no match for {pattern!r}")

    replace_once(r'(?m)^\(footprint "[^"]*"', lambda m: f'(footprint "{name}"')
    replace_once(r'(?m)^\t\(descr "[^"]*"\)', lambda m: f'\t(descr "{module["footprint_description"]}")')
    replace_once(r'(?m)^\t\(tags "[^"]*"\)', lambda m: f'\t(tags "{module["tags"]}")')
    replace_once(r'\(property "Value" "[^"]*"', lambda m: f'(property "Value" "{name}"')
    replace_once(r'\(model "[^"]*"', lambda m: f'(model "{model}"')
    _u = _make_u(_uuid.UUID(module["uuid_namespace"]))
    return re.sub(r'\(uuid "[^"]+"\)', lambda m: f'(uuid "{_u()}")', text)


def rot(x, y):
    """Quarter turn clockwise on the board (KiCad footprint coordinates have y down)."""
    return -y, x


def generate_footprint(module):
    """Build the complete .kicad_mod content string."""
    name = module["name"]
    pin_data = module["pad_data"]
    fp = module["footprint"]

    # KLC-driven parameters

    # ==================================================================
    # KLC F5.2: Fabrication layer requirements
    # Ordered first as other sections reference the mechanical outline
    # ==================================================================

    # KLC F5.2.1.a: Outline uses line width between {0.10mm and 0.15mm}
    # (recommended 0.10mm)
    fab_line_width = 0.1

    # KLC F5.2.2.b: Bevel should be 1mm or 25% of package size
    # (whichever is smaller)
    fab_chamfer = 1.0

    # ==================================================================
    # KLC F5.1: Silkscreen layer requirements
    # ==================================================================

    # KLC F5.1.1.a: Text size = 1.00mm
    silk_font_sz = (1, 1)

    # KLC F5.1.1.b: Text thickness = 0.15mm
    silk_font_thickness = 0.15

    # KLC F5.1.2.a: Silkscreen line width should nominally be 0.12mm
    silk_line_width = 0.12

    # KLC F5.1.4: For SMD footprints, silkscreen must be fully visible
    # after boards assembly (no silkscreen allowed under component)
    #
    # + KLC F5.4: Elements on the graphic layer should not overlap
    silk_offset = 0 + silk_line_width / 2 + fab_line_width / 2

    # KLC F5.1.Polarity.1.a: It should positioned inside the courtyard to
    # prevent silkscreen overlap
    silk_pin1_gap = 0
    silk_pin1_size = 0.5 - silk_line_width * 1.5

    # ==================================================================
    # KLC F5.3: Courtyard layer requirements
    # ==================================================================

    # F5.3.1: Courtyard uses 0.05mm line width
    crt_line_width = 0.05

    # F5.3.5
    # Unless otherwise specified, clearance is 0.25mm. This shrinks to
    # 0.15mm for parts smaller than 1.5mm in any dimension.
    #
    # Note: Increased for more assembly tolerance and to accommodate the
    # silkscreen gap on the pin-1 corner
    crt_offset = 0.5

    # KLC F6.3.1: By default, pads for SMD footprints should use
    # only the following layers
    pad_smd_layers = '"F.Cu" "F.Mask" "F.Paste"'

    pads_raw = pin_data["pads"]
    half_w = pin_data["mod_w"] / 2
    half_h = pin_data["mod_h"] / 2

    out = []
    _u = _make_u(_uuid.UUID(module["uuid_namespace"]))

    def w(line, indent=0):
        out.append("\t" * indent + line)

    # ------------------------------------------------------------------
    # Header
    # ------------------------------------------------------------------
    w(f'(footprint "{name}"')
    w('(version 20260206)', 1)
    w('(generator "pcbnew")', 1)
    w('(generator_version "10.0")', 1)
    w('(layer "F.Cu")', 1)
    w(f'(descr "{module["footprint_description"]}")', 1)
    w(f'(tags "{module["tags"]}")', 1)

    # ------------------------------------------------------------------
    # Properties (each with unlocked + uuid)
    # ------------------------------------------------------------------

    def _prop(name, value, at_x, at_y, layer, hide=False, font_sz=silk_font_sz):
        w(f'(property "{name}" "{value}"', 1)
        w(f'(at {_n(at_x)} {_n(at_y)} 0)', 2)
        w('(unlocked yes)', 2)
        w(f'(layer "{layer}")', 2)
        if hide:
            w('(hide yes)', 2)
        w(f'(uuid "{_u()}")', 2)
        w('(effects', 2)
        w('(font', 3)
        w(f'(size {font_sz[0]} {font_sz[1]})', 4)
        w(f'(thickness {silk_font_thickness})', 4)
        w(')', 3)
        w(')', 2)
        w(')', 1)

    text_y = half_w + 0.5 + 1            # 1 mm beyond the courtyard

    # KLC F5.1.1: Reference Designator must be drawn on F.SilkS layer
    _prop("Reference", "REF**",          0, -text_y, "F.SilkS")

    # KLC F5.2.3: Component value (footprint name) must be displayed on the F.Fab layer
    _prop("Value",     name,             0,  text_y, "F.Fab")

    _prop("Datasheet", "",               0,   0.0, "F.Fab", hide=True,
          font_sz=(1.27, 1.27))
    _prop("Description", "",             0,   0.0, "F.Fab", hide=True,
          font_sz=(1.27, 1.27))

    # ------------------------------------------------------------------
    # Attributes
    # ------------------------------------------------------------------

    # KLC F6.1: Footprint component type must be set to surface mount
    w('(attr smd)', 1)

    w('(duplicate_pad_numbers_are_jumpers no)', 1)

    # ------------------------------------------------------------------
    # KLC F5.1 Silkscreen layer requirements:
    # The silkscreen is printed to the external surface of a PCB to aid
    # in component identification and orientation. Typically this layer
    # contains the component RefDes to locate components on the board
    # after assembly.
    # ------------------------------------------------------------------
    sx = half_w + silk_offset
    sy = half_h + silk_offset

    # Simple rectangular silkscreen outline with gap on the pin-1 triangle corner
    silk_segs = [
        ((-sx + silk_line_width * 2, -sy), ( sx, -sy                      )),   # top
        (( sx,                       -sy), ( sx,  sy                      )),   # right
        (( sx,                        sy), (-sx,  sy                      )),   # bottom
        ((-sx,                        sy), (-sx, -sy + silk_line_width * 2)),   # left
    ]
    for (x1, y1), (x2, y2) in silk_segs:
        (x1, y1), (x2, y2) = rot(x1, y1), rot(x2, y2)
        w('(fp_line', 1)
        w(f'(start {_n(x1, 3)} {_n(y1, 3)})', 2)
        w(f'(end {_n(x2, 3)} {_n(y2, 3)})', 2)
        w('(stroke', 2)
        w(f'(width {silk_line_width})', 3)
        w('(type solid)', 3)
        w(')', 2)
        w('(layer "F.SilkS")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)

    # ------------------------------------------------------------------
    # KLC F5.1.6:
    # Polarity marking / Pin-1 designator must be drawn on the F.SilkS
    # layer
    # ------------------------------------------------------------------

    px, py = -sx - silk_pin1_gap, -sy - silk_pin1_gap
    tri = [rot(*p) for p in ((px, py), (px - silk_pin1_size, py), (px, py - silk_pin1_size), (px, py))]
    w('(fp_poly', 1)
    w('(pts', 2)
    w(' '.join(f'(xy {_n(x, 3)} {_n(y, 3)})' for x, y in tri), 3)
    w(')', 2)
    w('(stroke', 2)
    w(f'(width {silk_line_width})', 3)
    w('(type solid)', 3)
    w(')', 2)
    w('(fill yes)', 2)
    w('(layer "F.SilkS")', 2)
    w(f'(uuid "{_u()}")', 2)
    w(')', 1)

    # ------------------------------------------------------------------
    # KLC F5.2: Fabrication layer requirements
    # The fabrication layers are used to display the simplified
    # mechanical outline of components on the PCB.
    # ------------------------------------------------------------------

    # KLC F5.2.2: Footprint polarisation / location of pin-1 is drawn
    fab_verts = [
        (-half_w + fab_chamfer, -half_h               ),    # after chamfer on top edge
        ( half_w,               -half_h               ),    # top-right
        ( half_w,                half_h               ),    # bottom-right
        (-half_w,                half_h               ),    # bottom-left
        (-half_w,              -(half_h - fab_chamfer)),    # left edge up to chamfer
        (-half_w + fab_chamfer, -half_h               ),    # close back to start
    ]
    for i in range(len(fab_verts) - 1):
        x1, y1 = rot(*fab_verts[i])
        x2, y2 = rot(*fab_verts[i + 1])
        w('(fp_line', 1)
        w(f'(start {_n(x1, 3)} {_n(y1, 3)})', 2)
        w(f'(end {_n(x2, 3)} {_n(y2, 3)})', 2)
        w('(stroke', 2)
        w(f'(width {fab_line_width})', 3)
        w('(type solid)', 3)
        w(')', 2)
        w('(layer "F.Fab")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)

    # ------------------------------------------------------------------
    # F5.3 Courtyard layer requirements:
    # The component courtyard is defined as the smallest rectangular
    # area that provides a minimum electrical and mechanical clearance
    # around the combined component body and land pattern boundaries. It
    # is allowed to create a contoured courtyard area using a polygon
    # instead of a simple rectangle. (IPC-7351C)
    #
    # The courtyard should include any extra required clearance for
    # mating connectors (for example).
    # ------------------------------------------------------------------
    cx = half_w + crt_offset    # 16.25
    cy = half_h + crt_offset    # 21.50
    crt_segs = [
        ((-cx, -cy), ( cx, -cy)),
        (( cx, -cy), ( cx,  cy)),
        (( cx,  cy), (-cx,  cy)),
        ((-cx,  cy), (-cx, -cy)),
    ]
    for (x1, y1), (x2, y2) in crt_segs:
        (x1, y1), (x2, y2) = rot(x1, y1), rot(x2, y2)
        w('(fp_line', 1)

        # KLC F5.3.2: All courtyard line elements are placed on a 0.01mm
        # grid
        w(f'(start {_n(x1, 2)} {_n(y1, 2)})', 2)
        w(f'(end {_n(x2, 2)} {_n(y2, 2)})', 2)

        w('(stroke', 2)
        w(f'(width {crt_line_width})', 3)
        w('(type default)', 3)
        w(')', 2)
        w('(layer "F.CrtYd")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)

    # ------------------------------------------------------------------
    # User reference text on F.Fab
    # ------------------------------------------------------------------
    w('(fp_text user "${REFERENCE}"', 1)
    w('(at 0 0 0)', 2)
    w('(unlocked yes)', 2)
    w('(layer "F.Fab")', 2)
    w(f'(uuid "{_u()}")', 2)
    w('(effects', 2)
    w('(font', 3)
    w(f'(size {silk_font_sz[0]} {silk_font_sz[1]})', 4)
    w(f'(thickness {silk_font_thickness})', 4)
    w(')', 3)
    w(')', 2)
    w(')', 1)

    # ------------------------------------------------------------------
    # Copper pads
    # ------------------------------------------------------------------
    for pad in pads_raw:
        pin  = pad["pin"]
        pcx, pcy = rot(pad["cx"], -pad["cy"])   # negate Y for KiCad footprint coords
        pw   = pad["w"]
        ph   = pad["h"]

        # IPC Level B pad extensions: +0.1mm side, +0.1mm toe; width and height swap
        # with the quarter turn
        ipc_w = ph + 0.2
        ipc_h = pw + 0.2

        if pin == 1:
            w(f'(pad "{pin}" smd roundrect', 1)
            w(f'(at {_n(pcx, 2)} {_n(pcy, 2)})', 2)
            w(f'(size {_n(ipc_w, 2)} {_n(ipc_h, 2)})', 2)
            w(f'(layers {pad_smd_layers})', 2)
            w('(roundrect_rratio 0.25)', 2)
            w('(chamfer_ratio 0.444)', 2)
            w('(chamfer top_right)', 2)
            w(f'(uuid "{_u(f"pad:{pin}")}")', 2)
            w(')', 1)
        else:
            w(f'(pad "{pin}" smd roundrect', 1)
            w(f'(at {_n(pcx, 2)} {_n(pcy, 2)})', 2)
            w(f'(size {_n(ipc_w, 2)} {_n(ipc_h, 2)})', 2)
            w(f'(layers {pad_smd_layers})', 2)
            w('(roundrect_rratio 0.25)', 2)
            w(f'(uuid "{_u(f"pad:{pin}")}")', 2)
            w(')', 1)

    # ------------------------------------------------------------------
    # Module mechanical hole references on Dwgs.User
    # (module has alignment holes but PCB does not need NPTH drills)
    # ------------------------------------------------------------------
    for hx, hy in (rot(*h) for h in fp["alignment_holes"]):
        r = fp["alignment_hole_radius"]
        w('(fp_circle', 1)
        w(f'(center {_n(hx)} {_n(hy)})', 2)
        w(f'(end {_n(hx + r)} {_n(hy)})', 2)
        w('(stroke', 2)
        w('(width 0.1)', 3)
        w('(type solid)', 3)
        w(')', 2)
        w('(fill no)', 2)
        w('(layer "Dwgs.User")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)

    # ------------------------------------------------------------------
    # Ground stitching vias (2 per pad, +/-0.5 mm along long axis)
    # ------------------------------------------------------------------
    pad_by_pin = {p["pin"]: p for p in pads_raw}

    for vp in sorted(fp["via_pins"]):
        pad = pad_by_pin[vp]
        vx0 = pad["cx"]
        vy0 = -pad["cy"]
        pw  = pad["w"]
        ph  = pad["h"]

        if pw >= ph:
            # long axis is X
            offsets = [(vx0 - 0.5, vy0), (vx0 + 0.5, vy0)]
        else:
            # long axis is Y
            offsets = [(vx0, vy0 - 0.5), (vx0, vy0 + 0.5)]

        for vi, (vx, vy) in enumerate(rot(*o) for o in offsets):
            w(f'(pad "{pad["pin"]}" thru_hole circle', 1)
            w(f'(at {_n(vx, 2)} {_n(vy, 2)})', 2)
            w('(size 0.5 0.5)', 2)
            w('(drill 0.2)', 2)
            w('(layers "*.Cu")', 2)
            w('(remove_unused_layers no)', 2)
            w(f'(uuid "{_u(f"via:{vp}:{vi}")}")', 2)
            w(')', 1)

    # ------------------------------------------------------------------
    # 3D model + footer
    # ------------------------------------------------------------------
    w('(embedded_fonts no)', 1)
    w(f'(model "${{IRIDIUM_MODULES_3DMODEL_DIR}}/{LIB}.3dshapes/{name}.step"', 1)
    w('(offset', 2)
    w('(xyz 0 0 0)', 3)
    w(')', 2)
    w('(scale', 2)
    w('(xyz 1 1 1)', 3)
    w(')', 2)
    w('(rotate', 2)
    w('(xyz 0 0 0)', 3)
    w(')', 2)
    w(')', 1)

    w(')')

    return "\n".join(_fp_sort(out)) + "\n"


def footprint_name(module, variant=None):
    return module["name"] + (f"_{variant}" if variant else "")


def standoff_pads(spec, holes, _u):
    """Return the pad blocks of the upstream standoff footprint, repeated at each hole.

    The upstream solder ring is numbered "1", which would join connector pin 1, and a numbered pad
    adds to the pin count that KiCad's footprint filter compares, so it is unnumbered. Its three small
    circle pads lie inside the ring pad's own outline, so they are dropped; the ring is then one pad.
    """
    with open(upstream_path(spec["upstream"])) as f:
        lines = f.read().rstrip("\n").split("\n")
    blocks = []
    for line in lines:
        if line.startswith("\t(") and not line.startswith("\t\t"):
            blocks.append([])
        if blocks:
            blocks[-1].append(line)
    pads = ["\n".join(b) for b in blocks if _kind(b) == "pad" and not b[0].startswith('\t(pad "1" smd circle')]
    if not pads:
        raise SystemExit(f"{spec['upstream']}: no pads")
    out = []
    for h, (hx, hy) in enumerate(holes):
        for i, pad in enumerate(pads):
            pad = re.sub(r'(?m)^\t\t\(at (\S+) (\S+)\)',
                         lambda m: f"\t\t(at {_n(float(m[1]) + hx)} {_n(float(m[2]) + hy)})", pad, count=1)
            pad = re.sub(r'^\t\(pad "1"', '\t(pad ""', pad)
            uid = f'(uuid "{_u(f"standoff:{h}:{i}")}")'
            if "(uuid" in pad:
                pad = re.sub(r'\(uuid "[^"]+"\)', uid, pad)
            else:
                pad = re.sub(r'\n\t\)$', f"\n\t\t{uid}\n\t)", pad)
            out.extend(pad.split("\n"))
    return out


def generate_connector_footprint(module, variant=None):
    """Build the .kicad_mod of a board-to-board connector whose module mounts on two standoffs.

    The origin is the centre of the pad array, so a placement file reports the connector's
    position. The module outline, mounting holes and keep-outs are drawn relative to it.
    """
    fp = module["footprint"]
    spec = fp["variants"][variant] if variant else None
    name = footprint_name(module, variant)
    descr = spec["description"] if spec else module["footprint_description"]
    tags = module["tags"] + (f" {spec['tags']}" if spec else "")
    holes = fp["mounting_holes"]
    (ex0, ey0), (ex1, ey1) = fp["envelope"]
    (cx0, cy0), (cx1, cy1) = fp["courtyard"]
    embedded = bool(spec and "upstream" in spec)
    holes_drilled = spec is None
    (bx0, by0), (bx1, by1) = fp["body"]

    # KLC parameters, as in generate_footprint
    fab_width, silk_width, crt_width, thickness = 0.1, 0.12, 0.05, 0.15
    fab_chamfer = 1.0
    layers_smd = '"F.Cu" "F.Mask" "F.Paste"'

    out = []
    # Each variant has its own ids
    _u = _make_u(_uuid.uuid5(_uuid.UUID(module["uuid_namespace"]), name))

    def w(line, indent=0):
        out.append("\t" * indent + line)

    def prop(prop_name, value, x, y, layer, hide=False, size=1.0):
        w(f'(property "{prop_name}" "{value}"', 1)
        w(f'(at {_n(x)} {_n(y)} 0)', 2)
        w('(unlocked yes)', 2)
        w(f'(layer "{layer}")', 2)
        if hide:
            w('(hide yes)', 2)
        w(f'(uuid "{_u()}")', 2)
        w('(effects', 2)
        w('(font', 3)
        w(f'(size {size} {size})', 4)
        w(f'(thickness {thickness})', 4)
        w(')', 3)
        w(')', 2)
        w(')', 1)

    def line(x1, y1, x2, y2, layer, width):
        w('(fp_line', 1)
        w(f'(start {_n(x1, 3)} {_n(y1, 3)})', 2)
        w(f'(end {_n(x2, 3)} {_n(y2, 3)})', 2)
        w('(stroke', 2)
        w(f'(width {width})', 3)
        w('(type solid)', 3)
        w(')', 2)
        w(f'(layer "{layer}")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)

    def rect(x0, y0, x1, y1, layer, width):
        for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
            line(*a, *b, layer, width)

    def text(value, x, y, layer, size):
        w(f'(fp_text user "{value}"', 1)
        w(f'(at {_n(x)} {_n(y)} 0)', 2)
        w('(unlocked yes)', 2)
        w(f'(layer "{layer}")', 2)
        w(f'(uuid "{_u()}")', 2)
        w('(effects', 2)
        w('(font', 3)
        w(f'(size {size} {size})', 4)
        w(f'(thickness {thickness if size >= 1 else 0.1})', 4)
        w(')', 3)
        w(')', 2)
        w(')', 1)

    w(f'(footprint "{name}"')
    w('(version 20260206)', 1)
    w('(generator "pcbnew")', 1)
    w('(generator_version "10.0")', 1)
    w('(layer "F.Cu")', 1)
    w(f'(descr "{descr}")', 1)
    w(f'(tags "{tags}")', 1)

    mid_x = (ex0 + ex1) / 2
    prop("Reference", "REF**", (cx0 + cx1) / 2, cy0 - 1.0, "F.SilkS")
    prop("Value", name, (cx0 + cx1) / 2, ey1 + 1.0, "F.Fab")
    prop("Datasheet", "", 0, 0, "F.Fab", hide=True, size=1.27)
    prop("Description", "", 0, 0, "F.Fab", hide=True, size=1.27)
    w('(attr smd)', 1)
    w('(duplicate_pad_numbers_are_jumpers no)', 1)

    # Socket body outline with the pin 1 corner chamfered; pin 1 is on the +y row, at -x
    verts = [(bx0, by0), (bx1, by0), (bx1, by1), (bx0 + fab_chamfer, by1), (bx0, by1 - fab_chamfer), (bx0, by0)]
    for (x1, y1), (x2, y2) in zip(verts, verts[1:]):
        line(x1, y1, x2, y2, "F.Fab", fab_width)
    text("${REFERENCE}", (bx0 + bx1) / 2, 0, "F.Fab", 1.0)

    # Samtec's recommended silkscreen is the connector outline; the long sides would cross the pads
    for x in (bx0, bx1):
        line(x, by0, x, by1, "F.SilkS", silk_width)
    # The pin 1 marker is the one on the KiCad RF_Module ESP-12E footprint (0.48 x 0.336 mm), just
    # outside pad 1 and pointing at it
    p1 = min(module["pad_data"]["pads"], key=lambda p: p["pin"])
    tip = p1["y"] + p1["h"] / 2 + 0.3
    tri = [(p1["x"], tip), (p1["x"] - 0.24, tip + 0.336), (p1["x"] + 0.24, tip + 0.336), (p1["x"], tip)]
    w('(fp_poly', 1)
    w('(pts', 2)
    w(' '.join(f'(xy {_n(x, 3)} {_n(y, 3)})' for x, y in tri), 3)
    w(')', 2)
    w('(stroke', 2)
    w(f'(width {silk_width})', 3)
    w('(type solid)', 3)
    w(')', 2)
    w('(fill yes)', 2)
    w('(layer "F.SilkS")', 2)
    w(f'(uuid "{_u()}")', 2)
    w(')', 1)

    # The courtyard covers the socket and, where hardware is drilled or soldered, a circle at each hole
    # on the side of the board where the hardware sits
    rect(cx0, cy0, cx1, cy1, "F.CrtYd", crt_width)
    radii = spec if embedded else fp
    front_radius = radii["courtyard_radius" if embedded else "hole_courtyard_radius"]
    back_radius = radii["back_courtyard_radius" if embedded else "hole_back_courtyard_radius"]
    for crt_layer, crt_radius in (("F.CrtYd", front_radius), ("B.CrtYd", back_radius)):
        for hx, hy in holes:
            w('(fp_circle', 1)
            w(f'(center {_n(hx)} {_n(hy)})', 2)
            w(f'(end {_n(hx + crt_radius)} {_n(hy)})', 2)
            w('(stroke', 2)
            w(f'(width {crt_width})', 3)
            w('(type default)', 3)
            w(')', 2)
            w('(fill no)', 2)
            w(f'(layer "{crt_layer}")', 2)
            w(f'(uuid "{_u()}")', 2)
            w(')', 1)

    # The envelope is both the module's mechanical outline and the clearance limit, so it is on F.Fab and Dwgs.User.
    # Drawings: height zones, RF connector and the standoff positions. Notes are all caps.
    for layer in ("F.Fab", "Dwgs.User"):
        rect(ex0, ey0, ex1, ey1, layer, fab_width)
    zone = fp["height_zones"]
    band_y = zone["connector_band_y"]
    line(ex0, band_y, ex1, band_y, "Dwgs.User", fab_width)

    def stack(lines, x, y, size):
        """One text element with its anchor on the 1 mm grid; KiCad sets the line spacing."""
        text("\\n".join(lines), round(x), round(y), "Dwgs.User", size)

    rf = fp["rf_connector"]
    cx, cy = rf["center"]
    rect(cx - rf["size"][0] / 2, cy - rf["size"][1] / 2, cx + rf["size"][0] / 2, cy + rf["size"][1] / 2,
         "Dwgs.User", fab_width)
    band_mid = (band_y + ey1) / 2
    stack([f"{zone['module']} MM CLEARANCE UNDER MODULE"], mid_x, (ey0 + band_y) / 2, 0.7)
    stack([f"{zone['connector']} MM", "CLEARANCE"], (cx1 + ex1) / 2, band_mid, 0.6)
    stack([f"{zone['rf']} MM", "CLEARANCE", "UNDER RF", "CONNECTOR/", "CABLE"],
          (ex0 + cx - rf["size"][0] / 2) / 2, band_mid, 0.6)
    for hx, hy in [] if embedded else holes:
        w('(fp_circle', 1)
        w(f'(center {_n(hx)} {_n(hy)})', 2)
        w(f'(end {_n(hx + fp["keepout_diameter"] / 2)} {_n(hy)})', 2)
        w('(stroke', 2)
        w(f'(width {fab_width})', 3)
        w('(type solid)', 3)
        w(')', 2)
        w('(fill no)', 2)
        w('(layer "Dwgs.User")', 2)
        w(f'(uuid "{_u()}")', 2)
        w(')', 1)
        line(hx - 3.2, hy, hx + 3.2, hy, "Dwgs.User", fab_width)
        line(hx, hy - 3.2, hx, hy + 3.2, "Dwgs.User", fab_width)

    if not embedded:
        # One note for the hardware, midway between the holes. Each leader is a horizontal tail, then a
        # 45 degree run to an arrow tip on the grid just outside the hole and clear of the crosshair arms
        note = fp["hardware_note"]
        size, tail = 0.7, 1.0
        nx = round(sum(h[0] for h in holes) / len(holes))
        ny = round(holes[0][1] + 2.5)
        stack(note, nx, ny, size)
        for hx, hy in holes:
            side = 1 if hx < nx else -1
            tip_x, tip_y = round(hx + side * 1.5), round(hy + 1.5)
            x1 = tip_x + side * (ny - tip_y)
            x0 = x1 + side * tail
            if max(len(s) for s in note) * size * 0.99 / 2 + 0.3 > abs(x0 - nx):
                raise SystemExit("hardware_note is too wide for its leaders")
            line(x0, ny, x1, ny, "Dwgs.User", fab_width)
            line(x1, ny, tip_x, tip_y, "Dwgs.User", fab_width)
            ux, uy = -side * math.sqrt(0.5), -math.sqrt(0.5)
            for sign in (-1, 1):
                ang = math.radians(20) * sign
                bx = -(ux * math.cos(ang) - uy * math.sin(ang)) * 0.7
                by = -(ux * math.sin(ang) + uy * math.cos(ang)) * 0.7
                line(tip_x, tip_y, tip_x + bx, tip_y + by, "Dwgs.User", fab_width)

    for pad in module["pad_data"]["pads"]:
        w(f'(pad "{pad["pin"]}" smd roundrect', 1)
        w(f'(at {_n(pad["x"], 3)} {_n(pad["y"], 3)})', 2)
        w(f'(size {_n(pad["w"], 3)} {_n(pad["h"], 3)})', 2)
        w(f'(layers {layers_smd})', 2)
        w('(roundrect_rratio 0.25)', 2)
        w(f'(solder_paste_margin {fp["paste_margin"]})', 2)
        w(f'(uuid "{_u(f"pad:{pad["pin"]}")}")', 2)
        w(')', 1)

    if embedded:
        out.extend(standoff_pads(spec, holes, _u))
    elif holes_drilled:
        for h, (hx, hy) in enumerate(holes):
            w('(pad "" np_thru_hole circle', 1)
            w(f'(at {_n(hx)} {_n(hy)})', 2)
            w(f'(size {fp["hole_drill"]} {fp["hole_drill"]})', 2)
            w(f'(drill {fp["hole_drill"]})', 2)
            w('(layers "*.Cu" "*.Mask")', 2)
            w(f'(uuid "{_u(f"hole:{h}")}")', 2)
            w(')', 1)

    w('(embedded_fonts no)', 1)

    def model(path):
        w(f'(model "{path}"', 1)
        w('(offset', 2)
        w('(xyz 0 0 0)', 3)
        w(')', 2)
        w('(scale', 2)
        w('(xyz 1 1 1)', 3)
        w(')', 2)
        w('(rotate', 2)
        w('(xyz 0 0 0)', 3)
        w(')', 2)
        w(')', 1)

    model(f"${{IRIDIUM_MODULES_3DMODEL_DIR}}/{LIB}.3dshapes/{name}.step")
    w(')')
    return "\n".join(_fp_sort(out)) + "\n"


# ---------------------------------------------------------------------------
# Symbol
# ---------------------------------------------------------------------------
# Geometry constants (all in mm, KiCad symbol coords: +Y up)
# ---------------------------------------------------------------------------
SPACING    = 2.54
PIN_LENGTH = 2.54
FONT_SIZE  = 1.27

BODY_LEFT   = -15.24
BODY_RIGHT  =  15.24

# Left / right pin stub X
LEFT_X  = BODY_LEFT  - PIN_LENGTH
RIGHT_X = BODY_RIGHT + PIN_LENGTH


class _Geometry:
    """Symbol outline and pin row positions, derived from the module's layouts."""

    def __init__(self, left_layout, right_layout, margin_rows):
        n_rows = len(left_layout)
        # Left rows are centred on y = 0, topmost first
        self.left_y = [(n_rows - 1) / 2.0 * SPACING - i * SPACING for i in range(n_rows)]
        # Room above and below for the power pin names
        self.body_top = self.left_y[0] + margin_rows * SPACING
        # Right rows are top-aligned with the left side
        self.right_y = [self.left_y[0] - i * SPACING for i in range(len(right_layout))]
        self.body_bottom = min(self.left_y[-1], self.right_y[-1]) - margin_rows * SPACING
        # Centre the body on the origin; an odd shift in half rows would move the pins off the grid
        if round(-(self.body_top + self.body_bottom) / SPACING) % 2:
            self.body_bottom -= SPACING
        shift = -(self.body_top + self.body_bottom) / 2.0
        self.body_top += shift
        self.body_bottom += shift
        self.left_y = [y + shift for y in self.left_y]
        self.right_y = [y + shift for y in self.right_y]
        self.top_y = self.body_top + SPACING
        self.bottom_y = self.body_bottom - SPACING


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _r(v):
    """Format a float to 2 decimal places to avoid FP noise."""
    return _n(v, 2)


def _unstack(number):
    """Return the pin numbers covered by a plain or native-stack number."""
    if not number.startswith("["):
        return [number]
    out = []
    for part in number[1:-1].split(","):
        lo, _, hi = part.partition("-")
        out.extend(range(int(lo), int(hi or lo) + 1))
    return out


def _stack(numbers):
    """Return the native pin stack number for a sorted list of pin numbers."""
    runs, i = [], 0
    while i < len(numbers):
        j = i
        while j + 1 < len(numbers) and numbers[j + 1] == numbers[j] + 1:
            j += 1
        if j - i >= 2:
            runs.append(f"{numbers[i]}-{numbers[j]}")
        else:
            runs.extend(str(n) for n in numbers[i:j + 1])
        i = j + 1
    return "[" + ",".join(runs) + "]"


def _pin(pin_type, name, number, x, y, angle, hidden=False):
    """Return the s-expression block for one KiCad symbol pin."""
    hide = "\n\t\t\t\t(hide yes)" if hidden else ""
    return (
        f"\t\t\t(pin {pin_type} line\n"
        f"\t\t\t\t(at {_r(x)} {_r(y)} {angle})\n"
        f"\t\t\t\t(length {PIN_LENGTH}){hide}\n"
        f"\t\t\t\t(name \"{name}\"\n"
        f"\t\t\t\t\t(effects\n"
        f"\t\t\t\t\t\t(font\n"
        f"\t\t\t\t\t\t\t(size {FONT_SIZE} {FONT_SIZE})\n"
        f"\t\t\t\t\t\t)\n"
        f"\t\t\t\t\t)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t\t(number \"{number}\"\n"
        f"\t\t\t\t\t(effects\n"
        f"\t\t\t\t\t\t(font\n"
        f"\t\t\t\t\t\t\t(size {FONT_SIZE} {FONT_SIZE})\n"
        f"\t\t\t\t\t\t)\n"
        f"\t\t\t\t\t)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)"
    )


def _pin_sort_key(block):
    """Sort native pin stacks after plain pins, each group by number."""
    stacked, first = re.search(r'\(number "(\[?)(\d+)', block).groups()
    return bool(stacked), int(first)


def _property(name, value, x, y, hidden=False):
    """Return one property s-expression block."""
    hide = "\t\t\t(hide yes)\n" if hidden else ""
    return (
        f"\t\t(property \"{name}\" \"{value}\"\n"
        f"\t\t\t(at {_r(x)} {_r(y)} 0)\n"
        f"\t\t\t(show_name no)\n"
        f"\t\t\t(do_not_autoplace no)\n"
        f"{hide}"
        f"\t\t\t(effects\n"
        f"\t\t\t\t(font\n"
        f"\t\t\t\t\t(size {FONT_SIZE} {FONT_SIZE})\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)"
    )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------
def _symbol_block(module):
    """Return the s-expression text of one symbol, without a trailing newline."""
    name = module["name"]
    sym = module["symbol"]
    g = _Geometry(sym["left_layout"], sym["right_layout"], sym.get("margin_rows", 4))
    signals = {int(p): v for p, v in sym["signals"].items()}
    parts = []

    parts.append(
        f"\t(symbol \"{name}\"\n"
        "\t\t(exclude_from_sim no)\n"
        "\t\t(in_bom yes)\n"
        "\t\t(on_board yes)\n"
        "\t\t(in_pos_files yes)\n"
        "\t\t(duplicate_pin_numbers_are_jumpers no)"
    )

    ref_y = g.body_top + 1.27    # just above the body rectangle
    parts.append(_property("Reference",   "U",             -10.16, ref_y))
    parts.append(_property("Value",       name,             10.16, ref_y))
    parts.append(_property("Footprint",   f"{LIB}:{name}", 0, -5.08, hidden=True))
    parts.append(_property("Datasheet",   module["datasheet"], 0, -7.62, hidden=True))
    parts.append(_property("Description", module["description"], 0, 0, hidden=True))
    parts.append(_property("ki_keywords", module["keywords"], 0, 0, hidden=True))
    parts.append(_property("ki_fp_filters", module["ki_fp_filters"], 0, 0, hidden=True))

    # --- unit-0 drawing: body rectangle ---
    parts.append(
        f"\t\t(symbol \"{name}_0_1\"\n"
        f"\t\t\t(rectangle\n"
        f"\t\t\t\t(start {_r(BODY_LEFT)} {_r(g.body_top)})\n"
        f"\t\t\t\t(end {_r(BODY_RIGHT)} {_r(g.body_bottom)})\n"
        f"\t\t\t\t(stroke\n"
        f"\t\t\t\t\t(width 0.254)\n"
        f"\t\t\t\t\t(type default)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t\t(fill\n"
        f"\t\t\t\t\t(type background)\n"
        f"\t\t\t\t)\n"
        f"\t\t\t)\n"
        f"\t\t)"
    )

    # --- unit-1 pins ---
    pins = []

    for i, pin_num in enumerate(sym["left_layout"]):
        if pin_num is None:
            continue
        pin_name, ptype = signals[pin_num]
        pins.append(_pin(ptype, pin_name, str(pin_num), LEFT_X, g.left_y[i], 0))

    for i, pin_num in enumerate(sym["right_layout"]):
        if pin_num is None:
            continue
        pin_name, ptype = signals[pin_num]
        pins.append(_pin(ptype, pin_name, str(pin_num), RIGHT_X, g.right_y[i], 180))

    # Power groups: one native pin stack per net
    for net, group in sym["power"].items():
        numbers = group["pins"]
        number = str(numbers[0]) if len(numbers) == 1 else _stack(numbers)
        pins.append(_pin("power_in", net, number, group["top_x"], g.top_y, 270))

    pins.append(_pin("power_in", "GND", _stack(sym["gnd"]), 0, g.bottom_y, 90))

    # NC pins: hidden, spread in a grid inside the body
    nc_cols = 6
    nc_x0 = BODY_LEFT + 2.54
    nc_y0 = g.body_top - 2.54
    for idx, p in enumerate(sym.get("nc", [])):
        col = idx % nc_cols
        row = idx // nc_cols
        nx = nc_x0 + col * 2.54
        ny = nc_y0 - row * 2.54
        pins.append(_pin("no_connect", "NC", str(p), nx, ny, 270, hidden=True))

    pins.sort(key=_pin_sort_key)

    unit1 = f"\t\t(symbol \"{name}_1_1\"\n"
    unit1 += "\n".join(pins)
    unit1 += "\n\t\t)"
    parts.append(unit1)

    parts.append("\t\t(embedded_fonts no)")
    parts.append("\t)")
    return "\n".join(parts)


def generate_symbols(modules):
    """Build the complete .kicad_sym content string for the given modules."""
    header = (
        "(kicad_symbol_lib\n"
        "\t(version 20251024)\n"
        "\t(generator \"kicad_symbol_editor\")\n"
        "\t(generator_version \"10.0\")"
    )
    blocks = [_symbol_block(m) for m in sorted(modules, key=lambda m: m["name"])]
    return "\n".join([header] + blocks + [")"]) + "\n"


def main_footprint(module_path, out_path, variant=None):
    module = load_module(module_path)
    if module["footprint"].get("kind") == "connector":
        content = generate_connector_footprint(module, variant)
    elif variant:
        raise SystemExit(f"{module['name']}: footprint variants need footprint.kind connector")
    elif "footprint" in module.get("reuse", {}):
        content = derive_footprint(module)
    else:
        content = generate_footprint(module)
    _write(out_path, content)
    print(f"Footprint written to {out_path}")

    expect = (module["footprint"]["variants"][variant] if variant else {}).get("expect") or module["footprint"]["expect"]
    counts = {
        "smd": len(re.findall(r'\(pad "[0-9]+" smd', content)),
        "npth": content.count("np_thru_hole"),
        "vias": len(re.findall(r'\(pad "[^"]*" thru_hole circle', content)),
    }
    print(f"SMD pads:     {counts['smd']}  (expected {expect['smd']})")
    print(f"NPTH holes:   {counts['npth']}  (expected {expect['npth']})")
    print(f"Vias:         {counts['vias']}  (expected {expect['vias']})")
    for key, want in expect.items():
        assert counts[key] == want, f"Expected {want} {key}, got {counts[key]}"
    print("All counts OK.")


def main_symbol(module_paths, out_path):
    modules = [load_module(p) for p in module_paths]
    content = generate_symbols(modules)
    _write(out_path, content)
    print(f"Symbol library written to {out_path}")

    for module in modules:
        block = _symbol_block(module)
        pin_count = sum(len(_unstack(n)) for n in re.findall(r'\(number "([^"]+)"', block))
        want = module["symbol"]["expect_pins"]
        print(f"{module['name']}: {pin_count} pins (expected {want})")
        assert pin_count == want, f"{module['name']}: expected {want} pins, got {pin_count}"
    print("Pin counts OK.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    fp = sub.add_parser("footprint")
    fp.add_argument("--module", required=True)
    fp.add_argument("--variant", help="footprint.variants key; omit for the default footprint")
    fp.add_argument("--out", required=True)
    sym = sub.add_parser("symbol")
    sym.add_argument("--module", action="append", required=True)
    sym.add_argument("--out", required=True)
    args = ap.parse_args()
    if args.cmd == "footprint":
        main_footprint(args.module, args.out, args.variant)
    else:
        main_symbol(args.module, args.out)


if __name__ == "__main__":
    main()
