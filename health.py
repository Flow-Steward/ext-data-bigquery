"""Fast health check; no outbound calls."""

import json

print(json.dumps({"ok": True, "status": "healthy"}))
