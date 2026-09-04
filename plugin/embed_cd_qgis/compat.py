"""Qt 5 / Qt 6 differences, in one place.

The plugin supports QGIS 3 (PyQt5) and QGIS 4 (PyQt6), and the two disagree about exactly two
things we care about: where enum members live, and how a field's type is named. Both helpers
lived in three files each, copied verbatim; a fix to one copy silently left the others behind.
"""


def scoped(owner, category, name):
    """An enum member, under Qt 6's scoped name or Qt 5's flat one.

    Qt 6 moved enum members into a nested class (`Qt.ItemDataRole.UserRole`); Qt 5 has them
    directly on the owner (`Qt.UserRole`). Ask for the nested form first and fall back.
    """
    try:
        return getattr(getattr(owner, category), name)
    except AttributeError:
        return getattr(owner, name)


def qvariant(kind):
    """The field type for `kind` ("str", "float", "int"), as QGIS's vector API wants it.

    QGIS 4 takes `QMetaType.Type.*`; QGIS 3 takes the long-deprecated `QVariant.*`. Passing the
    wrong one creates a field of the wrong type rather than raising, so the mapping has to be
    explicit rather than inferred.
    """
    try:
        from qgis.PyQt.QtCore import QMetaType
        return {"str": QMetaType.Type.QString, "float": QMetaType.Type.Double,
                "int": QMetaType.Type.Int}[kind]
    except (ImportError, AttributeError, KeyError):
        from qgis.PyQt.QtCore import QVariant
        return {"str": QVariant.String, "float": QVariant.Double, "int": QVariant.Int}[kind]
