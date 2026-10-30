#!/usr/bin/env python3
"""3D model steps, driven by the "model" section of modules/<name>/module.json.

  can       extract the shielding can from the vendor STEP assembly, moved from
            the vendor frame into the KiCad 3D frame (a quarter turn about Z
            followed by the module's vendor.translate)
  assemble  build the module STEP: XCAF-coloured PCB box, shielding can, a
            rounded-rectangle label plate, and one face per filled label path.

Usage:
  python tools/model.py can --module modules/9704 --vendor VENDOR.step --out can.step
  python tools/model.py assemble --module modules/9704 --label LABEL.svg --can CAN.step --out OUT.step
"""

import argparse
import json
import math
import os
import re
import sys
import xml.etree.ElementTree as ET

from OCP.BRepBndLib import BRepBndLib
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeEdge, BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakeWire,
    BRepBuilderAPI_Transform,
)
from OCP.BRepGProp import BRepGProp
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox, BRepPrimAPI_MakePrism
from OCP.Bnd import Bnd_Box
from OCP.GProp import GProp_GProps
from OCP.Geom import Geom_BezierCurve
from OCP.Interface import Interface_Static
from OCP.Message import Message, Message_Gravity
from OCP.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCP.STEPCAFControl import STEPCAFControl_Reader, STEPCAFControl_Writer
from OCP.STEPControl import STEPControl_AsIs, STEPControl_Reader, STEPControl_Writer
from OCP.TColgp import TColgp_Array1OfPnt
from OCP.TCollection import TCollection_ExtendedString
from OCP.TDF import TDF_Label, TDF_LabelSequence
from OCP.TDataStd import TDataStd_Name
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopLoc import TopLoc_Location
from OCP.XCAFDoc import XCAFDoc_ColorType, XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.gp import gp_Ax1, gp_Dir, gp_Pln, gp_Pnt, gp_Trsf, gp_Vec


# ----------------------------------------------------------------------
# Shielding can
# ----------------------------------------------------------------------

SIZE_TOL = 0.05


def die(msg):
    sys.exit(f"model: {msg}")


def load_model_spec(module_dir):
    with open(os.path.join(module_dir, "module.json")) as f:
        spec = json.load(f).get("model")
    if spec is None or spec.get("kind") != "vendor-step+label":
        die(f"{module_dir} has no vendor-step+label model")
    return spec


def label_name(label):
    name = TDataStd_Name()
    if label.FindAttribute(TDataStd_Name.GetID_s(), name):
        return name.Get().ToExtString()
    return ""


def components(label):
    """Yield (referred label, location relative to `label`) per component."""
    seq = TDF_LabelSequence()
    XCAFDoc_ShapeTool.GetComponents_s(label, seq)
    for i in range(1, seq.Length() + 1):
        comp = seq.Value(i)
        ref = TDF_Label()
        if not XCAFDoc_ShapeTool.GetReferredShape_s(comp, ref):
            ref = comp
        yield ref, XCAFDoc_ShapeTool.GetLocation_s(comp)


def find_part(label, name, loc):
    """Depth-first search for a part named `name`; returns (label, location in the root frame)."""
    for ref, cloc in components(label):
        total = loc.Multiplied(cloc)
        if label_name(ref) == name:
            return ref, total
        found = find_part(ref, name, total)
        if found:
            return found
    return None


def vendor_to_kicad(translate):
    rot = gp_Trsf()
    rot.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), -math.pi / 2)
    move = gp_Trsf()
    move.SetTranslation(gp_Vec(*translate))
    return move.Multiplied(rot)


def main_can(args):
    vendor = load_model_spec(args.module)["vendor"]
    module_part, can_part = vendor["module_part"], vendor["can_part"]
    can_size = vendor["can_size"]
    doc = TDocStd_Document(TCollection_ExtendedString("STEP"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    print(f"Reading {args.vendor}")
    if not reader.ReadFile(args.vendor) == 1:
        die(f"cannot read {args.vendor}")
    if not reader.Transfer(doc):
        die(f"cannot transfer {args.vendor}")

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    if roots.Length() < 1:
        die("vendor file has no shapes")

    module = find_part(roots.Value(1), module_part, TopLoc_Location())
    if module is None:
        die(f"component {module_part} not found")
    can = find_part(module[0], can_part, TopLoc_Location())
    if can is None:
        die(f"component {can_part} not found inside {module_part}")

    can_label, can_loc = can
    shape = XCAFDoc_ShapeTool.GetShape_s(can_label).Moved(can_loc)
    xf = BRepBuilderAPI_Transform(shape, vendor_to_kicad(vendor["translate"]), True)
    xf.Build()
    shape = xf.Shape()

    box = Bnd_Box()
    BRepBndLib.Add_s(shape, box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    size = (x1 - x0, y1 - y0, z1 - z0)
    if any(abs(a - b) > SIZE_TOL for a, b in zip(size, can_size)):
        die("can bbox %.3f x %.3f x %.3f does not match expected %.1f x %.1f x %.1f"
            % (*size, *can_size))

    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    writer = STEPControl_Writer()
    writer.Transfer(shape, STEPControl_AsIs)
    print(f"Writing {args.out}")
    if writer.Write(args.out) != 1:
        die(f"cannot write {args.out}")
    print("can bbox (%.3f, %.3f, %.3f) -> (%.3f, %.3f, %.3f)" % (x0, y0, z0, x1, y1, z1))


# ----------------------------------------------------------------------
# Module assembly
# ----------------------------------------------------------------------

SVG_NS = 'http://www.w3.org/2000/svg'


def srgb_to_linear(v):
    return v ** 2.2


def parse_svg_path(d_str):
    """Parse SVG path 'd' attribute into list of sub-paths.

    Each sub-path is a list of segments: ('L', x1,y1,x2,y2) or
    ('C', x1,y1,cx1,cy1,cx2,cy2,x2,y2) or ('Z',).

    Returns list of sub-paths. For compound paths (evenodd), first sub-path
    is the outer contour; subsequent are holes.
    """
    tokens = re.findall(r'[MLCSQZmlcsqz]|-?\d+\.?\d*(?:[eE][+-]?\d+)?', d_str)
    sub_paths = []
    current = []
    cx, cy = 0.0, 0.0
    sx, sy = 0.0, 0.0  # start of current sub-path
    i = 0

    while i < len(tokens):
        cmd = tokens[i]
        i += 1

        if cmd == 'M':
            if current:
                sub_paths.append(current)
                current = []
            cx, cy = float(tokens[i]), float(tokens[i + 1])
            sx, sy = cx, cy
            i += 2
            # Implicit L commands after M
            while i < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                nx, ny = float(tokens[i]), float(tokens[i + 1])
                current.append(('L', cx, cy, nx, ny))
                cx, cy = nx, ny
                i += 2

        elif cmd == 'm':
            if current:
                sub_paths.append(current)
                current = []
            dx, dy = float(tokens[i]), float(tokens[i + 1])
            cx, cy = cx + dx, cy + dy
            sx, sy = cx, cy
            i += 2
            while i < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                dx, dy = float(tokens[i]), float(tokens[i + 1])
                nx, ny = cx + dx, cy + dy
                current.append(('L', cx, cy, nx, ny))
                cx, cy = nx, ny
                i += 2

        elif cmd == 'L':
            while i < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                nx, ny = float(tokens[i]), float(tokens[i + 1])
                current.append(('L', cx, cy, nx, ny))
                cx, cy = nx, ny
                i += 2

        elif cmd == 'l':
            while i < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                dx, dy = float(tokens[i]), float(tokens[i + 1])
                nx, ny = cx + dx, cy + dy
                current.append(('L', cx, cy, nx, ny))
                cx, cy = nx, ny
                i += 2

        elif cmd == 'C':
            while i + 5 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                cx1, cy1 = float(tokens[i]), float(tokens[i + 1])
                cx2, cy2 = float(tokens[i + 2]), float(tokens[i + 3])
                nx, ny = float(tokens[i + 4]), float(tokens[i + 5])
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 6

        elif cmd == 'c':
            while i + 5 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                dcx1, dcy1 = float(tokens[i]), float(tokens[i + 1])
                dcx2, dcy2 = float(tokens[i + 2]), float(tokens[i + 3])
                dx, dy = float(tokens[i + 4]), float(tokens[i + 5])
                cx1, cy1 = cx + dcx1, cy + dcy1
                cx2, cy2 = cx + dcx2, cy + dcy2
                nx, ny = cx + dx, cy + dy
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 6

        elif cmd == 'S':
            while i + 3 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                if current and current[-1][0] == 'C':
                    prev_cx2 = current[-1][5]
                    prev_cy2 = current[-1][6]
                    cx1 = 2 * cx - prev_cx2
                    cy1 = 2 * cy - prev_cy2
                else:
                    cx1, cy1 = cx, cy
                cx2, cy2 = float(tokens[i]), float(tokens[i + 1])
                nx, ny = float(tokens[i + 2]), float(tokens[i + 3])
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 4

        elif cmd == 's':
            while i + 3 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                if current and current[-1][0] == 'C':
                    prev_cx2 = current[-1][5]
                    prev_cy2 = current[-1][6]
                    cx1 = 2 * cx - prev_cx2
                    cy1 = 2 * cy - prev_cy2
                else:
                    cx1, cy1 = cx, cy
                cx2 = cx + float(tokens[i])
                cy2 = cy + float(tokens[i + 1])
                nx = cx + float(tokens[i + 2])
                ny = cy + float(tokens[i + 3])
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 4

        elif cmd == 'Q':
            while i + 3 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                qx, qy = float(tokens[i]), float(tokens[i + 1])
                nx, ny = float(tokens[i + 2]), float(tokens[i + 3])
                # Convert quadratic to cubic
                cx1 = cx + 2.0 / 3.0 * (qx - cx)
                cy1 = cy + 2.0 / 3.0 * (qy - cy)
                cx2 = nx + 2.0 / 3.0 * (qx - nx)
                cy2 = ny + 2.0 / 3.0 * (qy - ny)
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 4

        elif cmd == 'q':
            while i + 3 < len(tokens) and tokens[i] not in 'MLCSQZmlcsqz':
                dqx, dqy = float(tokens[i]), float(tokens[i + 1])
                dx, dy = float(tokens[i + 2]), float(tokens[i + 3])
                qx, qy = cx + dqx, cy + dqy
                nx, ny = cx + dx, cy + dy
                cx1 = cx + 2.0 / 3.0 * (qx - cx)
                cy1 = cy + 2.0 / 3.0 * (qy - cy)
                cx2 = nx + 2.0 / 3.0 * (qx - nx)
                cy2 = ny + 2.0 / 3.0 * (qy - ny)
                current.append(('C', cx, cy, cx1, cy1, cx2, cy2, nx, ny))
                cx, cy = nx, ny
                i += 4

        elif cmd in ('Z', 'z'):
            if abs(cx - sx) > 1e-6 or abs(cy - sy) > 1e-6:
                current.append(('L', cx, cy, sx, sy))
            cx, cy = sx, sy
            if current:
                current.append(('Z',))
                sub_paths.append(current)
                current = []

    if current:
        sub_paths.append(current)

    return sub_paths


def apply_svg_transform(x, y, matrix):
    """Apply SVG affine transform matrix(a,b,c,d,e,f) to a point."""
    a, b, c, d, e, f = matrix
    return a * x + c * y + e, b * x + d * y + f


def svg_display_to_step(svg_x, svg_y, offset):
    """Map SVG display coordinates to STEP model coordinates.

    x = svg_y + offset[0], y = svg_x + offset[1]; there is no negation, which
    would mirror the label.
    """
    return svg_y + offset[0], svg_x + offset[1]


def transform_point_to_step(src_x, src_y, matrix, offset):
    """Full pipeline: source SVG coords -> display -> STEP."""
    disp_x, disp_y = apply_svg_transform(src_x, src_y, matrix)
    return svg_display_to_step(disp_x, disp_y, offset)


def transform_segment_to_step(seg, matrix, offset):
    """Transform a path segment from SVG source coords to STEP coords."""
    if seg[0] == 'L':
        _, x1, y1, x2, y2 = seg
        sx1, sy1 = transform_point_to_step(x1, y1, matrix, offset)
        sx2, sy2 = transform_point_to_step(x2, y2, matrix, offset)
        return ('L', sx1, sy1, sx2, sy2)
    elif seg[0] == 'C':
        _, x1, y1, cx1, cy1, cx2, cy2, x2, y2 = seg
        sx1, sy1 = transform_point_to_step(x1, y1, matrix, offset)
        scx1, scy1 = transform_point_to_step(cx1, cy1, matrix, offset)
        scx2, scy2 = transform_point_to_step(cx2, cy2, matrix, offset)
        sx2, sy2 = transform_point_to_step(x2, y2, matrix, offset)
        return ('C', sx1, sy1, scx1, scy1, scx2, scy2, sx2, sy2)
    return seg


# ======================== OCP GEOMETRY ========================

def segments_to_wire(segments, z):
    """Create an OCP wire from a list of path segments at height z.
    Uses native bezier curves for compact, smooth STEP output.
    Returns wire or None on failure.
    """
    wire_builder = BRepBuilderAPI_MakeWire()
    edge_count = 0

    for seg in segments:
        if seg[0] == 'Z':
            continue
        elif seg[0] == 'L':
            _, x1, y1, x2, y2 = seg
            if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1e-10:
                continue
            edge = BRepBuilderAPI_MakeEdge(
                gp_Pnt(x1, y1, z), gp_Pnt(x2, y2, z)
            ).Edge()
            wire_builder.Add(edge)
            edge_count += 1
        elif seg[0] == 'C':
            _, x1, y1, cx1, cy1, cx2, cy2, x2, y2 = seg
            if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1e-10:
                continue
            poles = TColgp_Array1OfPnt(1, 4)
            poles.SetValue(1, gp_Pnt(x1, y1, z))
            poles.SetValue(2, gp_Pnt(cx1, cy1, z))
            poles.SetValue(3, gp_Pnt(cx2, cy2, z))
            poles.SetValue(4, gp_Pnt(x2, y2, z))
            bezier = Geom_BezierCurve(poles)
            edge = BRepBuilderAPI_MakeEdge(bezier).Edge()
            wire_builder.Add(edge)
            edge_count += 1

    if edge_count < 2 or not wire_builder.IsDone():
        return None
    return wire_builder.Wire()


def make_face_with_holes(outer_segments, hole_segment_lists, z):
    """Face from an outer contour with holes cut out; None if the outer wire fails."""
    outer_wire = segments_to_wire(outer_segments, z)
    if outer_wire is None:
        return None

    # Explicit +Z plane keeps the face normal pointing up
    plane = gp_Pln(gp_Pnt(0, 0, z), gp_Dir(0, 0, 1))
    face_maker = BRepBuilderAPI_MakeFace(plane, outer_wire)
    if not face_maker.IsDone():
        return None

    for hole_segments in hole_segment_lists:
        hole_wire = segments_to_wire(hole_segments, z)
        if hole_wire is None:
            continue
        face_maker = BRepBuilderAPI_MakeFace(face_maker.Face(), hole_wire)

    return face_maker.Face()


def make_rounded_rect_pts(xmin, xmax, ymin, ymax, r, n_arc=8):
    """Generate points for a rounded rectangle."""
    pts = []
    # Bottom-left corner
    for i in range(n_arc + 1):
        angle = math.pi + i * (math.pi / 2) / n_arc
        pts.append((xmin + r + r * math.cos(angle), ymin + r + r * math.sin(angle)))
    # Bottom-right corner
    for i in range(n_arc + 1):
        angle = 3 * math.pi / 2 + i * (math.pi / 2) / n_arc
        pts.append((xmax - r + r * math.cos(angle), ymin + r + r * math.sin(angle)))
    # Top-right corner
    for i in range(n_arc + 1):
        angle = 0 + i * (math.pi / 2) / n_arc
        pts.append((xmax - r + r * math.cos(angle), ymax - r + r * math.sin(angle)))
    # Top-left corner
    for i in range(n_arc + 1):
        angle = math.pi / 2 + i * (math.pi / 2) / n_arc
        pts.append((xmin + r + r * math.cos(angle), ymax - r + r * math.sin(angle)))
    return pts


def load_svg_faces(svg_path, label):
    """Return (faces, failed, too_small) for the filled paths in the SVG."""
    matrix, offset, z = label["svg_matrix"], label["svg_to_step_offset"], label["z"]
    root = ET.parse(svg_path).getroot()
    faces = []
    failed = 0
    too_small = 0

    for elem in root.iter(f'{{{SVG_NS}}}path'):
        d_str = elem.get('d', '')
        if not d_str:
            continue
        sub_paths = [
            [transform_segment_to_step(seg, matrix, offset) for seg in sp]
            for sp in parse_svg_path(d_str)
        ]
        if not sub_paths:
            continue

        # First sub-path is the outer contour, the rest are holes
        if len(sub_paths) == 1:
            wire = segments_to_wire(sub_paths[0], z)
            maker = BRepBuilderAPI_MakeFace(wire) if wire is not None else None
            face = maker.Face() if maker is not None and maker.IsDone() else None
        else:
            face = make_face_with_holes(sub_paths[0], sub_paths[1:], z)
        if face is None:
            failed += 1
            continue

        props = GProp_GProps()
        BRepGProp.SurfaceProperties_s(face, props)
        if abs(props.Mass()) < 0.001:
            too_small += 1
            continue
        faces.append(face)

    return faces, failed, too_small


def load_can(can_path):
    reader = STEPControl_Reader()
    if reader.ReadFile(can_path) != 1:
        sys.exit(f"model: cannot read {can_path}")
    reader.TransferRoots()
    solids = []
    exp = TopExp_Explorer(reader.OneShape(), TopAbs_SOLID)
    while exp.More():
        solids.append(exp.Current())
        exp.Next()
    if len(solids) != 1:
        sys.exit(f"model: {can_path} has {len(solids)} solids, expected 1")
    # Coloured as a bare solid; XCAF does not propagate colours from compounds
    return solids[0]


def make_plate(plate):
    (x_min, x_max), (y_min, y_max) = plate["x"], plate["y"]
    z_min, z_max = plate["z"]
    pts = make_rounded_rect_pts(x_min, x_max, y_min, y_max, plate["corner_r"])
    wire_builder = BRepBuilderAPI_MakeWire()
    for i, (x1, y1) in enumerate(pts):
        x2, y2 = pts[(i + 1) % len(pts)]
        if (x2 - x1) ** 2 + (y2 - y1) ** 2 < 1e-12:
            continue
        wire_builder.Add(BRepBuilderAPI_MakeEdge(
            gp_Pnt(x1, y1, z_min), gp_Pnt(x2, y2, z_min)
        ).Edge())
    face = BRepBuilderAPI_MakeFace(wire_builder.Wire()).Face()
    return BRepPrimAPI_MakePrism(face, gp_Vec(0, 0, z_max - z_min)).Shape()


def quarter_turn(shape):
    """Rotate 90 degrees clockwise seen from above, matching the footprint's rotation."""
    trsf = gp_Trsf()
    trsf.SetRotation(gp_Ax1(gp_Pnt(0, 0, 0), gp_Dir(0, 0, 1)), -math.pi / 2)
    return BRepBuilderAPI_Transform(shape, trsf, True).Shape()


def main_assemble(args):
    spec = load_model_spec(args.module)
    print(f"Reading {args.label}")
    label_faces, failed, too_small = load_svg_faces(args.label, spec["label"])
    print(f"Created {len(label_faces)} faces, {failed} failed, {too_small} too small")
    can = load_can(args.can)
    plate = make_plate(spec["label"]["plate"])
    pcb = BRepPrimAPI_MakeBox(gp_Pnt(*spec["pcb"]["min"]), gp_Pnt(*spec["pcb"]["max"])).Shape()

    doc = TDocStd_Document(TCollection_ExtendedString("STEP"))
    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())
    color_type = XCAFDoc_ColorType.XCAFDoc_ColorSurf

    def rgb(r, g, b):
        return Quantity_Color(srgb_to_linear(r), srgb_to_linear(g), srgb_to_linear(b), Quantity_TOC_RGB)

    pcb_color = rgb(0x14 / 255.0, 0x33 / 255.0, 0x24 / 255.0)
    can_color = rgb(0.8, 0.8, 0.8)
    white_color = Quantity_Color(1.0, 1.0, 1.0, Quantity_TOC_RGB)
    # Pure black is dropped by XCAF
    label_color = rgb(0.10, 0.10, 0.10)

    # Each face is added and coloured on its own; compound colours do not propagate
    print(f"Adding {len(label_faces) + 3} shapes")
    for shape, color in [(pcb, pcb_color), (can, can_color), (plate, white_color)]:
        color_tool.SetColor(shape_tool.AddShape(quarter_turn(shape)), color, color_type)
    for face in label_faces:
        color_tool.SetColor(shape_tool.AddShape(quarter_turn(face)), label_color, color_type)

    writer = STEPCAFControl_Writer()
    writer.SetColorMode(True)
    writer.SetNameMode(True)
    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    writer.Transfer(doc, STEPControl_AsIs)
    print(f"Writing {args.out}")
    if writer.Write(args.out) != 1:
        sys.exit(f"model: cannot write {args.out}")

    check = STEPControl_Reader()
    check.ReadFile(args.out)
    check.TransferRoots()
    box = Bnd_Box()
    BRepBndLib.Add_s(check.OneShape(), box)
    x0, y0, z0, x1, y1, z1 = box.Get()
    print(f"Wrote {args.out}, bbox ({x0:.2f},{y0:.2f},{z0:.2f}) -> ({x1:.2f},{y1:.2f},{z1:.2f})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("can", help="extract the shielding can from the vendor STEP")
    p.add_argument("--module", required=True, help="module directory, e.g. modules/9704")
    p.add_argument("--vendor", required=True, help="vendor assembly STEP")
    p.add_argument("--out", required=True, help="output STEP with the can only")
    p = sub.add_parser("assemble", help="build the module STEP from the can and label")
    p.add_argument("--module", required=True, help="module directory, e.g. modules/9704")
    p.add_argument("--label", required=True, help="label SVG")
    p.add_argument("--can", required=True, help="shielding can STEP")
    p.add_argument("--out", required=True, help="output STEP")
    args = ap.parse_args()
    # The STEP writer prints a statistics banner per transferred shape
    for printer in Message.DefaultMessenger_s().Printers():
        printer.SetTraceLevel(Message_Gravity.Message_Warning)
    {"can": main_can, "assemble": main_assemble}[args.cmd](args)


if __name__ == "__main__":
    main()
