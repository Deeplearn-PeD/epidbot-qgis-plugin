"""EpidBot QGIS plugin.

Lets QGIS users chat with EpidBot (https://github.com/epidbot) through its
REST API: submit questions about public health and epidemiological data,
browse chat sessions, and bring generated plots into the current project.

This project is a standalone repository; it only talks to EpidBot over HTTP
and does not import the epidbot Python package.
"""

__version__ = "0.1.0"


def classFactory(iface):
    """QGIS plugin entry point.

    :param iface: QgisInterface instance provided by QGIS.
    :returns: An instantiated plugin object.
    """
    from .plugin import EpidbotPlugin

    return EpidbotPlugin(iface)
