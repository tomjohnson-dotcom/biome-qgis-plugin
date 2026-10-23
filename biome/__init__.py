def classFactory(iface):
    from .bng_plugin import BNGPlugin
    return BNGPlugin(iface)
