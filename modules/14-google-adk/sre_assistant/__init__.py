"""Makes this directory an agent ADK's tooling can discover.

The one line below is required. `adk web` imports the package, then looks for
`agent.root_agent` — without this import the package has no `agent` attribute
and discovery fails silently, which is the failure mode agent.py describes.
"""

from . import agent
