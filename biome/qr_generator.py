"""
qr_generator.py
Generates a QR code SVG string using the `qrcode` library.

WARNING: `qrcode` is NOT bundled with QGIS — verified absent on both 3.44.8 and
4.2.0 (this docstring used to claim it "ships with QGIS on all platforms",
which is what led the Field Mapper landing page to depend on a CDN instead).
Callers MUST handle ImportError. If `qrcode` is installed into the QGIS Python
environment this module starts working with no other change, and
desk_study_tab.build_landing_page will prefer it over the CDN automatically.
"""

def url_to_qr_svg(url: str, cell: int = 8) -> str:
    """Return an SVG string of a QR code encoding *url*."""
    import qrcode

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_L,
        box_size=1,
        border=2,
    )
    qr.add_data(url)
    qr.make(fit=True)
    matrix = qr.get_matrix()

    n    = len(matrix)
    size = n * cell
    rects = []
    for r, row in enumerate(matrix):
        for c, val in enumerate(row):
            if val:
                rects.append(
                    f'<rect x="{c*cell}" y="{r*cell}" '
                    f'width="{cell}" height="{cell}"/>'
                )

    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {size} {size}" '
        f'width="{size}" height="{size}" style="background:white">'
        f'<g fill="black">{"".join(rects)}</g>'
        f'</svg>'
    )
