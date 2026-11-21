"""Code 128 barcode encoding, as SVG.

Why hand-rolled: a barcode is a lookup table plus arithmetic, and the two
libraries that do it pull a Pillow dependency for an image format the browser
renders natively as SVG. SVG also stays crisp at any printer resolution, which
is the whole point of a scannable label.

Code 128B is used: it covers ASCII 32-126, which is every barcode a shop
actually prints (digits, letters, hyphens). Code sets A and C are omitted
because mixing them adds state transitions to encode and to test for no
benefit on a product label.

The symbology is defined in ISO/IEC 15417. The width table below is the
standard 107-symbol set: each entry is six bar/space widths in modules.
"""
from __future__ import annotations

from html import escape

# The 107 Code 128 symbol patterns, six module widths each, starting with the
# space width. Index 0 is the space character, 103 starts Code B, 106 is stop.
_PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312",
    "132212", "221213", "221312", "231212", "112232", "122132", "122231", "113222",
    "123122", "123221", "223211", "221132", "221231", "213212", "223112", "312131",
    "311222", "321122", "321221", "312212", "322112", "322211", "212123", "212321",
    "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121",
    "313121", "211331", "231131", "213113", "213311", "213131", "311123", "311321",
    "331121", "312113", "312311", "332111", "314111", "221411", "431111", "111224",
    "111422", "121124", "121421", "141122", "141221", "112214", "112412", "122114",
    "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112",
    "421211", "212141", "214121", "412121", "111143", "111341", "131141", "114113",
    "114311", "411113", "411311", "113141", "114131", "311141", "411131", "211412",
    "211214", "211232", "2331112",
)

_START_B = 104
_STOP = 106

# Bar widths are in modules, and a label prints them at a fixed module size
# rather than stretching the barcode to fill the space: below roughly 0.25 mm per
# module an inkjet or a worn thermal head merges the bars and the scanner reads
# nothing. That is also why ``width_mm`` exists — a code too long for its label
# has to be caught before printing, not squeezed.
MODULE_MM = 0.25


def is_encodable(value: str) -> bool:
    """True when every character has a Code 128B symbol."""
    return bool(value) and all(32 <= ord(ch) <= 126 for ch in value)


def encode(value: str) -> str:
    """Return the module string for ``value``: 1s are bars, 0s are spaces.

    Raises ``ValueError`` for a character outside Code 128B, because silently
    dropping it would print a barcode that scans to something other than the
    label's own text — the worst possible failure for a stock label.
    """
    if not value:
        raise ValueError("لا يمكن توليد باركود من نص فارغ")
    if not is_encodable(value):
        raise ValueError("الباركود يدعم الحروف الإنجليزية والأرقام والرموز فقط")

    codes = [_START_B]
    for ch in value:
        codes.append(ord(ch) - 32)
    # The check symbol is the weighted sum modulo 103: the start value counts
    # once, then each symbol is multiplied by its 1-based position.
    checksum = codes[0] + sum(index * code for index, code in enumerate(codes[1:], start=1))
    codes.append(checksum % 103)
    codes.append(_STOP)

    modules: list[str] = []
    for code in codes:
        pattern = _PATTERNS[code]
        bar = True
        for width in pattern:
            modules.append(("1" if bar else "0") * int(width))
            bar = not bar
    return "".join(modules)


def width_mm(value: str) -> float:
    """The width ``value`` prints at, in millimetres, at the nominal module size."""
    return len(encode(value)) * MODULE_MM


def fits(value: str, available_mm: float) -> bool:
    """True when ``value`` prints at a scannable size inside ``available_mm``."""
    return is_encodable(value) and width_mm(value) <= available_mm


def to_svg(value: str, *, height_mm: float = 12.0) -> str:
    """Render ``value`` as an inline SVG barcode.

    The viewBox is in modules, and the element is sized in millimetres at the
    nominal module width so the bars never compress below a scannable size;
    ``max-width:100%`` only guards against overflowing a narrow label.
    ``shape-rendering="crispEdges"``
    stops the browser from anti-aliasing the bars into grey, which is what makes
    a printed barcode fail to scan. The human-readable text is left to the
    caller: stretching the SVG to fill a label would stretch the glyphs too.
    """
    modules = encode(value)
    width = len(modules)
    bars = []
    index = 0
    while index < width:
        if modules[index] == "1":
            run = index
            while run < width and modules[run] == "1":
                run += 1
            bars.append(f'<rect x="{index}" y="0" width="{run - index}" height="100" />')
            index = run
        else:
            index += 1

    return (
        f'<svg class="barcode" viewBox="0 0 {width} 100" '
        f'preserveAspectRatio="none" role="img" aria-label="{escape(value)}" '
        f'style="height:{height_mm}mm;width:{width_mm(value):.1f}mm;max-width:100%" '
        f'shape-rendering="crispEdges">'
        f'<g fill="#000">{"".join(bars)}</g></svg>'
    )
