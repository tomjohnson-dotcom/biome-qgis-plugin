# -*- coding: utf-8 -*-
"""One shared-edge test used by merging and condition-change rendering."""

from qgis.core import QgsGeometry, QgsWkbTypes


EDGE_TOLERANCE = 0.001       # 1 mm: removes numerical noise between operations
MIN_SHARED_EDGE = 0.01       # 1 cm: rejects a genuine corner touch


def valid_geometry(geometry):
    return (geometry if (geometry is None or geometry.isEmpty()
                         or geometry.isGeosValid()) else geometry.makeValid())


def normalise_geometry(geometry):
    """Make valid and snap to the merger's 1 mm precision grid."""
    geometry = valid_geometry(geometry)
    if geometry is None or geometry.isEmpty():
        return geometry
    try:
        snapped = geometry.snappedToGrid(EDGE_TOLERANCE, EDGE_TOLERANCE)
        return (valid_geometry(snapped)
                if snapped is not None and not snapped.isEmpty() else geometry)
    except Exception:
        return geometry


def boundary_geometry(geometry):
    """Return a QgsGeometry boundary on both QGIS 3 and QGIS 4."""
    try:
        return geometry.boundary()
    except AttributeError:
        return QgsGeometry(geometry.constGet().boundary())


def shared_edge_geometry(first, second):
    """Return the real shared line, or None for separation/corner contact.

    This is the merger's established edge rule: geometries are snapped to a
    1 mm grid, then a 1 mm boundary buffer catches a vertex lying microscopically
    off the middle of the neighbouring segment.  A result must exceed 1 cm.
    """
    first = normalise_geometry(first)
    second = normalise_geometry(second)
    if (first is None or second is None or first.isEmpty() or second.isEmpty()):
        return None

    try:
        direct = first.intersection(second) if first.intersects(second) else None
        if (direct is not None and not direct.isEmpty()
                and QgsWkbTypes.geometryType(direct.wkbType()) == QgsWkbTypes.LineGeometry
                and direct.length() > MIN_SHARED_EDGE):
            return direct
    except Exception:
        pass

    try:
        first_boundary = boundary_geometry(first)
        second_boundary = boundary_geometry(second)
        shared = first_boundary.intersection(
            second_boundary.buffer(EDGE_TOLERANCE, 2))
        if (not shared.isEmpty()
                and QgsWkbTypes.geometryType(shared.wkbType()) == QgsWkbTypes.LineGeometry
                and shared.length() > MIN_SHARED_EDGE):
            return shared
    except Exception:
        pass
    return None


def has_shared_edge(first, second):
    return shared_edge_geometry(first, second) is not None

