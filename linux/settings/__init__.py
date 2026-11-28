"""The Linux settings window (GTK 4 + libadwaita), split per device page, capability editor and drawing.

pages/: per device kind, the state logic (pending edits, Apply order, bytes). editors/: one per capability.
drawings/: the artwork per model. No module imports GTK at import time; squire_settings.py loads it."""
