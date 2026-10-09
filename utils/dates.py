"""Small date helpers shared across routes."""

from datetime import datetime


def iso_week(dt_str):
    """ISO week label ('2026-W33') for a 'YYYY-MM-DD HH:MM:SS' string."""
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return "Unknown"
    iso = dt.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"
