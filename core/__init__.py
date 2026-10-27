"""Core (non-UI) modules for the EpidBot QGIS plugin.

Modules here avoid importing Qt wherever possible so the business logic can
be unit-tested outside a QGIS runtime:

- :mod:`epidbot_qgis.core.api_client` - pure Python HTTP client for the
  EpidBot REST API (uses ``requests``, which ships with QGIS).
- :mod:`epidbot_qgis.core.auth_store` - settings persistence; only lazily
  imports ``QSettings`` (a plain backend can be injected for tests).
- :mod:`epidbot_qgis.core.map_context` - pure prompt-context builder plus a
  lazily-imported QGIS collector.
- :mod:`epidbot_qgis.core.markdown_lite` - pure Markdown-to-HTML subset
  converter used by the chat UI.
- :mod:`epidbot_qgis.core.chat_worker` - the only Qt-dependent module
  (``QThread`` glue between the UI and the API client).
"""
