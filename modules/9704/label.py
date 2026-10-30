#!/usr/bin/env python3
"""Build the label SVG (artwork for the 3D model) from fetched fonts and logos.

Inputs outside the repository:
  --assets DIR  Iridium_Satellite_LLC_logo.svg, Conformite_Europeenne_logo.svg
  --fonts DIR   MyriadPro-Regular.otf, MyriadPro-Semibold.otf, MyriadPro-Bold.otf, timesbd.ttf
Output: filled paths in source coordinates with a shared transform, fill-rule evenodd.
"""

import argparse
import math
import os
import re
import xml.etree.ElementTree as ET

import uharfbuzz as hb
from fontTools.pens.basePen import decomposeQuadraticSegment
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.ttLib import TTFont
from shapely.geometry import LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union


# ----------------------------------------------------------------------
# Label geometry measured from the printed label in Iridium's manual
# ----------------------------------------------------------------------

MATRIX = (0.11336544, 0.08415391, 0.12969845, -0.17843548, -92.83972366, 44.27718702)
VIEWBOX = (5.48, -32.31, 36.50, 25.50)
SIZE_PX = (730, 510)

# Ink box (x0, y0, x1, y1) of each text line, in display coordinates
TEXT_INK = {
    "model": (5.92998, -25.59107, 18.71573, -23.80405),
    "fcc": (5.92080, -23.16351, 18.78242, -21.74270),
    "ic": (5.92086, -21.56941, 17.67706, -20.33941),
    "assembled": (5.87402, -19.64825, 18.96768, -18.62784),
    "contact": (5.80219, -18.06558, 21.86957, -17.04382),
    "phone": (5.86107, -16.58683, 15.39150, -15.63406),
    "rohs": (26.39228, -16.65433, 40.67794, -15.37338),
    "irid": (13.96892, -14.08113, 22.16390, -12.85720),
    "sn": (13.92396, -12.15125, 23.24116, -10.86964),
    "imei": (13.96956, -10.19226, 32.73712, -8.97883),
    "iccid": (13.96870, -8.27054, 38.47207, -7.03781),
}

CE_INK = (26.28659, -24.06151, 33.29127, -19.05565)

# GMPCS-MoU roundel: centre and outer radius, display coordinates
SEAL_CENTER = (38.1691, -21.5598)
SEAL_RADIUS = 2.4841

# Stroked outlines, in source coordinates
STROKE_WIDTH = 0.71
LEFT_BOX = [(350.969699, 453.216532), (320.972872, 479.435363),
            (362.243115, 498.900744), (392.239941, 472.674342)]
RIGHT_BOX = [(518.973124, 554.192723), (534.622655, 561.574577),
             (523.182674, 571.576043), (507.533143, 564.194189)]

# Corner mark: rounded square (centre, side, corner radius in display coordinates), stroked
CORNER_MARK = ((40.7301, -31.0612), 1.0, 0.25)


# ----------------------------------------------------------------------
# SVG path helpers
# ----------------------------------------------------------------------

def parse(d):
    """Path 'd' -> list of sub-paths, each a list of ('M'|'L'|'C', points)."""
    tok = re.findall(r"[MLCZ]|-?\d+\.?\d*(?:[eE][+-]?\d+)?", d)
    subs, cur, cmd, i = [], None, None, 0
    while i < len(tok):
        if tok[i] in "MLCZ":
            cmd = tok[i]
            i += 1
        if cmd == "M":
            cur = [("M", [(float(tok[i]), float(tok[i + 1]))])]
            subs.append(cur)
            i += 2
            cmd = "L"
        elif cmd == "L":
            cur.append(("L", [(float(tok[i]), float(tok[i + 1]))]))
            i += 2
        elif cmd == "C":
            cur.append(("C", [(float(tok[i + k]), float(tok[i + k + 1])) for k in (0, 2, 4)]))
            i += 6
        else:
            cmd = None
    return subs


def flatten(sub, n=24):
    pts = [sub[0][1][0]]
    for cmd, p in sub[1:]:
        if cmd == "L":
            pts.append(p[0])
        else:
            (x0, y0), (x1, y1), (x2, y2), (x3, y3) = pts[-1], *p
            for k in range(1, n + 1):
                t = k / n
                u = 1 - t
                pts.append((u**3 * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t**3 * x3,
                            u**3 * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t**3 * y3))
    return pts


def inside(pt, poly):
    x, y = pt
    ins, j = False, len(poly) - 1
    for i in range(len(poly)):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi) + xi:
            ins = not ins
        j = i
    return ins


def to_d(sub, fmt):
    return " ".join(cmd + " " + " ".join(fmt(x, y) for x, y in pts) for cmd, pts in sub) + " Z"


def group_paths(subs, fmt):
    """One 'd' string per outer contour (even nesting depth), followed by its direct holes."""
    polys = [flatten(s) for s in subs]
    depth = [sum(inside(polys[i][0], polys[j]) for j in range(len(subs)) if j != i)
             for i in range(len(subs))]
    out = []
    for i, s in enumerate(subs):
        if depth[i] % 2:
            continue
        d = to_d(s, fmt)
        for j, h in enumerate(subs):
            if j != i and depth[j] == depth[i] + 1 and inside(polys[j][0], polys[i]):
                d += " " + to_d(h, fmt)
        out.append(d)
    return out


def arcs_to_curves(d):
    """Rewrite an absolute M/L/H/V/A/C/Z path using only M/L/C/Z (arcs become cubic Beziers)."""
    import math

    tok = re.findall(r"[MLHVACZ]|-?\d+\.?\d*(?:[eE][+-]?\d+)?", d)
    out, i, cmd, cx, cy, sx, sy = [], 0, None, 0.0, 0.0, 0.0, 0.0
    f = lambda v: "%.6f" % v
    while i < len(tok):
        if tok[i] in "MLHVACZ":
            cmd = tok[i]
            i += 1
            if cmd == "Z":
                out.append("Z")
                cx, cy = sx, sy
                continue
        if cmd == "M":
            cx, cy = float(tok[i]), float(tok[i + 1])
            sx, sy = cx, cy
            out.append(f"M {f(cx)} {f(cy)}")
            i += 2
            cmd = "L"
        elif cmd == "L":
            cx, cy = float(tok[i]), float(tok[i + 1])
            out.append(f"L {f(cx)} {f(cy)}")
            i += 2
        elif cmd == "H":
            cx = float(tok[i])
            out.append(f"L {f(cx)} {f(cy)}")
            i += 1
        elif cmd == "V":
            cy = float(tok[i])
            out.append(f"L {f(cx)} {f(cy)}")
            i += 1
        elif cmd == "C":
            out.append("C " + " ".join(f(float(t)) for t in tok[i:i + 6]))
            cx, cy = float(tok[i + 4]), float(tok[i + 5])
            i += 6
        elif cmd == "A":
            rx, ry, rot, large, sweep, x, y = (float(t) for t in tok[i:i + 7])
            i += 7
            if rx != ry or rot != 0:
                raise ValueError("only circular arcs are supported")
            # endpoint -> centre parameterisation
            dx, dy = (cx - x) / 2, (cy - y) / 2
            r = rx
            q = dx * dx + dy * dy
            if q > r * r:
                r = math.sqrt(q)
            k = math.sqrt(max(r * r / q - 1, 0)) if q else 0
            if bool(large) == bool(sweep):
                k = -k
            ccx = k * dy + (cx + x) / 2
            ccy = -k * dx + (cy + y) / 2
            a0 = math.atan2(cy - ccy, cx - ccx)
            a1 = math.atan2(y - ccy, x - ccx)
            da = a1 - a0
            if sweep and da < 0:
                da += 2 * math.pi
            if not sweep and da > 0:
                da -= 2 * math.pi
            n = max(1, math.ceil(abs(da) / (math.pi / 2) - 1e-9))
            step = da / n
            t = 4 / 3 * math.tan(step / 4)
            for s in range(n):
                b0, b1 = a0 + s * step, a0 + (s + 1) * step
                p0 = (ccx + r * math.cos(b0), ccy + r * math.sin(b0))
                p3 = (ccx + r * math.cos(b1), ccy + r * math.sin(b1))
                p1 = (p0[0] - t * r * math.sin(b0), p0[1] + t * r * math.cos(b0))
                p2 = (p3[0] + t * r * math.sin(b1), p3[1] - t * r * math.cos(b1))
                out.append("C " + " ".join(f(v) for v in (*p1, *p2, *p3)))
            cx, cy = x, y
    return " ".join(out)


def source_formatter(matrix):
    """Return fmt(x, y) mapping label display coordinates to source coordinates as 'X Y'."""
    a, b, c, d, e, f = matrix
    det = a * d - b * c

    def fmt(x, y):
        X, Y = x - e, y - f
        return "%.5f %.5f" % ((d * X - c * Y) / det, (-b * X + a * Y) / det)

    return fmt


# ----------------------------------------------------------------------
# Text lines
# ----------------------------------------------------------------------

FONT_FILES = {"regular": "MyriadPro-Regular.otf", "semibold": "MyriadPro-Semibold.otf", "bold": "MyriadPro-Bold.otf"}

# name: (text, weight); ink boxes are in TEXT_INK
LINES = {
    "model": ("Model 9704", "bold"),
    "fcc": ("FCC ID: Q639704", "bold"),
    "ic": ("IC: 4629A-9704", "bold"),
    "assembled": ("Assembled in Thailand", "regular"),
    "contact": ("To Contact Iridium Satellite:", "regular"),
    "phone": ("+1.480.752.5155", "regular"),
    "rohs": ("RoHS Compliant/Pb Free", "regular"),
    "irid": ("IRID0258A", "bold"),
    "sn": ("S/N: 000000", "bold"),
    "imei": ("IMEI: " + "0" * 15, "bold"),
    "iccid": ("ICCID: " + "0" * 20, "bold"),
}

_fonts = {}


def font_path(fonts_dir, weight):
    return os.path.join(fonts_dir, FONT_FILES[weight])


def _font(fonts_dir, weight):
    path = font_path(fonts_dir, weight)
    if path not in _fonts:
        _fonts[path] = (TTFont(path), hb.Font(hb.Face(hb.Blob.from_file_path(path))))
    return _fonts[path]


def _contours(fonts_dir, weight, text):
    """Glyph outlines for `text` in font units (y up) at their pen positions, plus advances."""
    tt, hbfont = _font(fonts_dir, weight)
    buf = hb.Buffer()
    buf.add_str(text)
    buf.guess_segment_properties()
    hb.shape(hbfont, buf, {"kern": True, "liga": False})
    glyphset = tt.getGlyphSet()
    order = tt.getGlyphOrder()
    glyphs, pen_x = [], 0
    for info, pos in zip(buf.glyph_infos, buf.glyph_positions):
        rec = DecomposingRecordingPen(glyphset)
        glyphset[order[info.codepoint]].draw(rec)
        glyphs.append((pen_x + pos.x_offset, rec.value))
        pen_x += pos.x_advance
    return glyphs


def _subpaths(value, dx):
    """RecordingPen value -> sub-paths of ('M'|'L'|'C', points) shifted by dx."""
    subs, cur = [], None
    for op, args in value:
        if op == "moveTo":
            cur = [("M", [(args[0][0] + dx, args[0][1])])]
            subs.append(cur)
        elif op == "lineTo":
            cur.append(("L", [(args[0][0] + dx, args[0][1])]))
        elif op == "curveTo":
            cur.append(("C", [(x + dx, y) for x, y in args]))
        elif op == "qCurveTo":
            raise ValueError("TrueType outlines are not supported")
    return subs


def _ink_bbox(subs):
    pts = [p for s in subs for p in flatten(s, 16)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def line_paths(fonts_dir, text, weight, ink, matrix):
    """Return 'd' strings (source coords) for `text` fitted to ink = (x0, y0, x1, y1) in display coords."""
    to_source = source_formatter(matrix)
    glyphs = _contours(fonts_dir, weight, text)

    # natural layout, font units
    natural = []
    for dx, value in glyphs:
        natural += _subpaths(value, dx)
    nx0, ny0, nx1, ny1 = _ink_bbox(natural)

    scale = (ink[3] - ink[1]) / (ny1 - ny0)
    n_gaps = max(len(glyphs) - 1, 1)
    # tracking, in font units added to every pen advance (glyph origins shift cumulatively)
    track = ((ink[2] - ink[0]) / scale - (nx1 - nx0)) / n_gaps

    fmt = lambda x, y: to_source(ink[0] + (x - nx0) * scale, ink[3] - (y - ny0) * scale)

    out = []
    for i, (dx, value) in enumerate(glyphs):
        out += group_paths(_subpaths(value, dx + i * track), fmt)
    return out


# ----------------------------------------------------------------------
# GMPCS-MoU roundel
# ----------------------------------------------------------------------

CENTER = SEAL_CENTER
R_OUT, R_IN = SEAL_RADIUS, 1.5739

CAP_HEIGHT = 0.5447
RING_WEIGHT = "semibold"
# text: (string, glyph centre angles in degrees (y up), baseline radius, clockwise with letters pointing outward?)
# the cap-height band is centred between the ring's inner and outer edges
R_MID = (R_OUT + R_IN) / 2
TOP = ("GMPCS-MOU", [164.224, 142.437, 121.764, 104.444, 87.637, 73.459, 55.723, 33.849, 13.649],
       R_MID - CAP_HEIGHT / 2, True)
BOTTOM = ("REGISTRY", [-147.655, -130.165, -111.925, -96.048, -82.433, -65.278, -47.363, -30.112],
          R_MID + CAP_HEIGHT / 2, False)
DOTS = [(-171.6, R_MID), (-11.2, R_MID)]
DOT_DIAMETER = 0.194

ITU_TEXT = "ITU"
ITU_WIDTH, ITU_HEIGHT = 2.8098, 1.0992
ITU_INK = (CENTER[0] - ITU_WIDTH / 2, CENTER[1] - ITU_HEIGHT / 2,
           CENTER[0] + ITU_WIDTH / 2, CENTER[1] + ITU_HEIGHT / 2)
ITU_FONT_FILE = "timesbd.ttf"

KAPPA = 0.5522847498


def _circle(cx, cy, r):
    p = [(cx + r, cy), (cx, cy + r), (cx - r, cy), (cx, cy - r)]
    k = KAPPA * r
    segs = [("M", [p[0]])]
    for i in range(4):
        a, b = p[i], p[(i + 1) % 4]
        # tangent directions for a counter-clockwise walk
        ta = (-(a[1] - cy), a[0] - cx)
        tb = (-(b[1] - cy), b[0] - cx)
        n = r
        segs.append(("C", [(a[0] + ta[0] / n * k, a[1] + ta[1] / n * k),
                           (b[0] - tb[0] / n * k, b[1] - tb[1] / n * k), b]))
    return segs


def _outline(font, glyph_name, glyphset):
    """Glyph outline as sub-paths of ('M'|'L'|'C', pts) in font units (y up)."""
    rec = DecomposingRecordingPen(glyphset)
    glyphset[glyph_name].draw(rec)
    subs, cur, pos = [], None, None
    for op, args in rec.value:
        if op == "moveTo":
            cur = [("M", [args[0]])]
            subs.append(cur)
            pos = args[0]
        elif op == "lineTo":
            cur.append(("L", [args[0]]))
            pos = args[0]
        elif op == "curveTo":
            cur.append(("C", list(args)))
            pos = args[2]
        elif op == "qCurveTo":
            pts = list(args)
            if pts[-1] is None:  # all off-curve contour
                raise ValueError("contour without on-curve points")
            for off, on in decomposeQuadraticSegment(pts):
                p0 = pos
                cur.append(("C", [(p0[0] + 2 / 3 * (off[0] - p0[0]), p0[1] + 2 / 3 * (off[1] - p0[1])),
                                  (on[0] + 2 / 3 * (off[0] - on[0]), on[1] + 2 / 3 * (off[1] - on[1])), on]))
                pos = on
    return subs


def _glyph_height(font, char, glyphset):
    """Height of a glyph's ink, for fonts whose OS/2 table has no sCapHeight."""
    pts = [p for s in _outline(font, font.getBestCmap()[ord(char)], glyphset) for p in flatten(s, 12)]
    return max(p[1] for p in pts) - min(p[1] for p in pts)


def _glyphs(font_path, text):
    """[(sub-paths, advance, ink centre x, ink bbox)] in font units, plus capHeight and unitsPerEm."""
    font = TTFont(font_path)
    cmap, hmtx, gs = font.getBestCmap(), font["hmtx"], font.getGlyphSet()
    out, pen = [], 0
    for ch in text:
        name = cmap[ord(ch)]
        subs = _outline(font, name, gs)
        pts = [p for s in subs for p in flatten(s, 12)]
        if pts:
            x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
            y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
        else:
            x0 = x1 = y0 = y1 = 0
        out.append((subs, pen, (x0 + x1) / 2, (x0, y0, x1, y1)))
        pen += hmtx[name][0]
    cap = getattr(font["OS/2"], "sCapHeight", 0) or _glyph_height(font, "H", gs)
    return out, cap, font["head"].unitsPerEm


def _linear_angles(glyphs, a_first, a_last):
    centres = [g[1] + g[2] for g in glyphs]
    return [a_first + (c - centres[0]) / (centres[-1] - centres[0]) * (a_last - a_first) for c in centres]


def _ring_text(fonts_dir, text, weight, angles, r_base, clockwise, cap_height=None):
    """Glyph contours (display coords, y down), one list per glyph, set on an arc.

    angles: centre angle of each glyph in degrees (y up), or (first, last) to space by advance widths.
    """
    path = font_path(fonts_dir, weight)
    glyphs, cap, _ = _glyphs(path, text)
    scale = (cap_height or CAP_HEIGHT) / cap
    if len(angles) == 2:
        angles = _linear_angles(glyphs, *angles)
    out = []
    for (subs, _, xc, _), deg in zip(glyphs, angles):
        phi = math.radians(deg)
        er = (math.cos(phi), math.sin(phi))
        et = (math.sin(phi), -math.cos(phi)) if clockwise else (-math.sin(phi), math.cos(phi))
        up = 1 if clockwise else -1

        def place(x, y):
            u = (x - xc) * scale
            v = y * scale
            px = er[0] * (r_base + up * v) + et[0] * u
            py = er[1] * (r_base + up * v) + et[1] * u
            return (CENTER[0] + px, CENTER[1] - py)

        out.append([[(cmd, [place(*p) for p in pts]) for cmd, pts in s] for s in subs])
    return out


def _itu(fonts_dir):
    glyphs, cap, _ = _glyphs(os.path.join(fonts_dir, ITU_FONT_FILE), ITU_TEXT)
    ink = ITU_INK
    ys = [g[3][1] for g in glyphs] + [g[3][3] for g in glyphs]
    y0, y1 = min(ys), max(ys)
    scale = (ink[3] - ink[1]) / (y1 - y0)
    gap = (ink[2] - ink[0]) / scale
    first, last = glyphs[0], glyphs[-1]
    natural = (last[1] + last[3][2]) - (first[1] + first[3][0])
    track = (gap - natural) / (len(glyphs) - 1)
    out = []
    left = first[1] + first[3][0]
    for i, (subs, pen, _, _) in enumerate(glyphs):
        for s in subs:
            out.append([(cmd, [(ink[0] + (x + pen + i * track - left) * scale,
                                ink[3] - (y - y0) * scale) for x, y in pts]) for cmd, pts in s])
    return out


def _roundel_contours(fonts_dir):
    subs = [_circle(*CENTER, R_OUT), _circle(*CENTER, R_IN)]
    for text, angles, rb, cw in (TOP, BOTTOM):
        for glyph in _ring_text(fonts_dir, text, RING_WEIGHT, angles, rb, cw):
            subs += glyph
    for ang, r in DOTS:
        px = CENTER[0] + r * math.cos(math.radians(ang))
        py = CENTER[1] - r * math.sin(math.radians(ang))
        subs.append(_circle(px, py, DOT_DIAMETER / 2))
    return subs + _itu(fonts_dir)


def roundel_paths(fonts_dir, matrix):
    """'d' strings (source coords, even-odd fill) for the roundel at its display position."""
    return group_paths(_roundel_contours(fonts_dir), source_formatter(matrix))


# ----------------------------------------------------------------------
# Iridium logo
# ----------------------------------------------------------------------

# Net translation of the logo's two nested <g transform="translate()"> groups
GROUP_TX, GROUP_TY = -234 + 235.39561, -594.00154 + 596.32755

# Logo -> label display coordinates: uniform scale (from the horizontal extent) and
# translation, matching the measured extremes: left edge of the first dot,
# right edge of the registered mark, and the vertical centre of the top/bottom extremes
SCALE = 0.1024620125
OFFSET_X, OFFSET_Y = 13.4746503138, -31.6385244965


def logo_paths(svg_file, matrix):
    """svg_file: Iridium_Satellite_LLC_logo.svg; matrix: label source -> display."""
    to_source = source_formatter(matrix)
    fmt = lambda x, y: to_source(SCALE * (x + GROUP_TX) + OFFSET_X, SCALE * (y + GROUP_TY) + OFFSET_Y)

    results = []
    for p in ET.parse(svg_file).getroot().iter():
        if p.tag.endswith("path"):
            results += group_paths(parse(p.get("d")), fmt)
    return results


# ----------------------------------------------------------------------
# CE mark
# ----------------------------------------------------------------------

def mark_paths(svg_file, ink, matrix):
    """ink = (x0, y0, x1, y1) in label display coordinates."""
    to_source = source_formatter(matrix)

    curves = [arcs_to_curves(p.get("d"))
              for p in ET.parse(svg_file).getroot().iter() if p.tag.endswith("path")]
    subs = [s for d_str in curves for s in parse(d_str)]
    pts = [p for s in subs for p in flatten(s, 60)]
    mx0, my0 = min(p[0] for p in pts), min(p[1] for p in pts)
    mx1, my1 = max(p[0] for p in pts), max(p[1] for p in pts)
    sx = (ink[2] - ink[0]) / (mx1 - mx0)
    sy = (ink[3] - ink[1]) / (my1 - my0)

    fmt = lambda x, y: to_source(ink[0] + (x - mx0) * sx, ink[1] + (y - my0) * sy)
    out = []
    for d_str in curves:
        out += group_paths(parse(d_str), fmt)
    return out


# ----------------------------------------------------------------------
# Stroked boxes and corner mark
# ----------------------------------------------------------------------

def _ring_d(coords):
    pts = list(coords)[:-1]
    return "M " + " L ".join("%.5f %.5f" % p for p in pts) + " Z"


def _polygon_d(poly):
    return " ".join([_ring_d(poly.exterior.coords)] + [_ring_d(r.coords) for r in poly.interiors])


def _to_source(x, y):
    a, b, c, d, e, f = MATRIX
    det = a * d - b * c
    X, Y = x - e, y - f
    return (d * X - c * Y) / det, (-b * X + a * Y) / det


def box_paths():
    """'d' strings (even-odd fill) for the two boxes and the corner mark."""
    half = STROKE_WIDTH / 2
    shapes = [LineString(pts + pts[:1]).buffer(half, quad_segs=8) for pts in (LEFT_BOX, RIGHT_BOX)]

    (cx, cy), side, radius = CORNER_MARK
    inner = side / 2 - radius
    square = box(cx - inner, cy - inner, cx + inner, cy + inner).buffer(radius, quad_segs=16)
    mark = Polygon([_to_source(x, y) for x, y in square.exterior.coords])
    shapes.append(mark.buffer(half, quad_segs=8))

    merged = unary_union(shapes)
    polys = merged.geoms if isinstance(merged, MultiPolygon) else [merged]
    return [_polygon_d(p) for p in polys if not p.is_empty]


# ----------------------------------------------------------------------
# SVG assembly and CLI
# ----------------------------------------------------------------------

def build(fonts_dir, assets_dir):
    m = MATRIX
    paths = logo_paths(os.path.join(assets_dir, "Iridium_Satellite_LLC_logo.svg"), m)
    paths += mark_paths(os.path.join(assets_dir, "Conformite_Europeenne_logo.svg"), CE_INK, m)
    paths += roundel_paths(fonts_dir, m)
    for name, (text, weight) in LINES.items():
        paths += line_paths(fonts_dir, text, weight, TEXT_INK[name], m)
    paths += box_paths()
    return paths


def write_svg(paths, out):
    ns = "http://www.w3.org/2000/svg"
    ET.register_namespace("", ns)
    root = ET.Element("{%s}svg" % ns, {
        "width": str(SIZE_PX[0]), "height": str(SIZE_PX[1]),
        "viewBox": " ".join("%.2f" % v for v in VIEWBOX), "version": "1.1"})
    transform = "matrix(%s)" % ",".join("%.8f" % v for v in MATRIX)
    for d in paths:
        ET.SubElement(root, "{%s}path" % ns, {
            "d": d, "fill": "rgb(0%, 0%, 0%)", "fill-rule": "evenodd", "stroke": "none",
            "transform": transform})
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    ET.ElementTree(root).write(out, xml_declaration=True, encoding="utf-8")


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    third_party = os.path.join(os.path.dirname(os.path.dirname(here)), "third_party")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fonts", default=os.path.join(third_party, "fonts"))
    ap.add_argument("--assets", default=os.path.join(third_party, "logos"), help="directory holding the fetched logo SVGs")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    paths = build(args.fonts, args.assets)
    write_svg(paths, args.out)
    print(f"{len(paths)} paths written to {args.out}")


if __name__ == "__main__":
    main()
