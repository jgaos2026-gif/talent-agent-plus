"""JGA Enterprise branding — one source of truth for the app.

From John's taste corpus (exhibit #1, his actual logo):
  black-and-gold, classical serif, luxury weight.
  Gold serif on black. No other palette is used in this app.
"""
BRAND = "JGA Enterprise"
PRODUCT = "Talent Agent Plus Complete"
COLORS = {"background": "#0a0a0a", "panel": "#111111", "gold": "#d4af37",
          "gold_dark": "#c9a227", "cream": "#f5f0e1", "muted_gold": "#b8a97a"}
FONTS = "Georgia, 'Times New Roman', serif"
STYLE = "classical serif, luxury weight"
TAGLINE = "Agent · Merchant Store · Promoter · Producer · Secretary"
CONTACT = {"city": "Peoria, Illinois", "phone": "(779) 396-6934",
           "email": "johnarenz@jaysgraphicarts.com"}


def header_text() -> str:
    return f"{BRAND} — {PRODUCT} · {TAGLINE}"


def css() -> str:
    return (f"body{{margin:0;background:{COLORS['background']};color:{COLORS['cream']};"
            f"font-family:{FONTS}}} h1,h2{{color:{COLORS['gold']}}} "
            f".card{{border:1px solid {COLORS['gold_dark']};background:{COLORS['panel']};"
            f"border-radius:8px;padding:18px 22px;margin:18px 0}}")
