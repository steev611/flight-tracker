"""HTML email rendering — produces a polished message body alongside the plain text.

We embed the aircraft photo by URL (planespotters.net) rather than as an
attachment, so the email stays light. Email clients block remote images by
default; the photo only appears once the recipient trusts the sender, which
is fine.
"""

import html
import requests
from typing import Optional

_PHOTO_CACHE: dict[str, Optional[str]] = {}
_PHOTO_TIMEOUT = 5

BANNER_COLOR = {
    "takeoff":            ("#16a34a", "TAKEOFF"),            # green
    "landing":            ("#1d4ed8", "LANDED"),             # blue
    "in_flight_progress": ("#ca8a04", "IN FLIGHT"),          # amber
    "signal_lost":        ("#6b7280", "SIGNAL LOST"),        # gray
    "emergency_squawk":   ("#dc2626", "EMERGENCY SQUAWK"),   # red
}


def lookup_photo(reg: str) -> Optional[str]:
    """Get a photo URL for this tail from planespotters.net. Cached per process."""
    if reg in _PHOTO_CACHE:
        return _PHOTO_CACHE[reg]
    url = None
    try:
        r = requests.get(f"https://api.planespotters.net/pub/photos/reg/{reg}",
                         timeout=_PHOTO_TIMEOUT)
        if r.status_code == 200:
            photos = r.json().get("photos") or []
            if photos:
                # Prefer the 280-wide preview over the tiny thumbnail.
                url = photos[0].get("thumbnail_large", {}).get("src") \
                    or photos[0].get("thumbnail", {}).get("src")
    except Exception:
        url = None
    _PHOTO_CACHE[reg] = url
    return url


def render(event_type: str, *,
           reg: str, aircraft_summary: str, body_rows: list[tuple[str, str]],
           summary_subtitle: str, live_url: str,
           detected_at: str, photo_url: Optional[str]) -> str:
    """Return an HTML body for an event email.

    `body_rows` is a list of (label, value) pairs displayed as a table.
    """
    color, banner = BANNER_COLOR.get(event_type, ("#374151", event_type.upper()))
    photo_html = ""
    if photo_url:
        photo_html = (
            f'<img src="{html.escape(photo_url)}" alt="{html.escape(reg)}"'
            f' style="width:160px;height:auto;border-radius:6px;'
            f'object-fit:cover;float:right;margin:0 0 12px 16px;">'
        )

    rows_html = "".join(
        f'<tr><td style="padding:6px 12px 6px 0;color:#6b7280;white-space:nowrap;'
        f'vertical-align:top;">{html.escape(label)}</td>'
        f'<td style="padding:6px 0;font-weight:500;">{html.escape(str(value))}</td></tr>'
        for label, value in body_rows
    )

    return f"""\
<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#111827;">
<table role="presentation" cellpadding="0" cellspacing="0" border="0" width="100%" style="background:#f3f4f6;padding:24px 0;">
  <tr><td align="center">
    <table role="presentation" cellpadding="0" cellspacing="0" border="0" width="600" style="max-width:600px;background:#ffffff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,0.08);">
      <tr><td style="background:{color};padding:18px 24px;color:#ffffff;">
        <div style="font-size:12px;letter-spacing:1px;opacity:0.85;text-transform:uppercase;">{html.escape(banner)}</div>
        <div style="font-size:24px;font-weight:700;margin-top:4px;">{html.escape(reg)}</div>
        <div style="font-size:14px;opacity:0.9;margin-top:2px;">{html.escape(summary_subtitle)}</div>
      </td></tr>
      <tr><td style="padding:24px;">
        {photo_html}
        <div style="font-size:14px;color:#374151;margin-bottom:12px;">{html.escape(aircraft_summary)}</div>
        <table role="presentation" cellpadding="0" cellspacing="0" border="0" style="width:100%;font-size:14px;border-collapse:collapse;">
          {rows_html}
        </table>
        <div style="clear:both;"></div>
        <div style="margin-top:24px;text-align:center;">
          <a href="{html.escape(live_url)}" style="display:inline-block;background:{color};color:#ffffff;text-decoration:none;padding:10px 22px;border-radius:6px;font-weight:600;font-size:14px;">Track live on globe.adsb.lol &rarr;</a>
        </div>
      </td></tr>
      <tr><td style="padding:12px 24px;background:#f9fafb;color:#6b7280;font-size:12px;border-top:1px solid #e5e7eb;">
        Detected at {html.escape(detected_at)} &middot; flight-tracker
      </td></tr>
    </table>
  </td></tr>
</table>
</body></html>"""
