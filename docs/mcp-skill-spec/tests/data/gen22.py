"""Generate synthetic UI screenshots for use case 22. Run: .venv/bin/python gen22.py"""
from PIL import Image, ImageDraw, ImageFont
import os
D = os.path.dirname(os.path.abspath(__file__))
def font(n):
    for p in ["/System/Library/Fonts/Helvetica.ttc", "/System/Library/Fonts/Supplemental/Arial.ttf"]:
        try: return ImageFont.truetype(p, n)
        except Exception: pass
    return ImageFont.load_default(n)

def canvas(title):
    im = Image.new("RGB", (800, 500), "white"); d = ImageDraw.Draw(im)
    d.rectangle([0, 0, 800, 36], fill="#e5e7eb"); d.text((12, 8), title, fill="black", font=font(16))
    return im, d

def button(d, box, text, fill, fg="white"):
    d.rounded_rectangle(box, 6, fill=fill); d.text((box[0] + 14, box[1] + 9), text, fill=fg, font=font(20))

# login wall
im, d = canvas("https://news.example.com/article/42")
d.text((40, 60), "Sign in to continue reading", fill="black", font=font(34))
d.text((40, 110), "You have reached your free article limit.", fill="#444", font=font(20))
d.rectangle([40, 160, 440, 200], outline="#888"); d.text((50, 170), "Email", fill="#888", font=font(18))
d.rectangle([40, 215, 440, 255], outline="#888"); d.text((50, 225), "Password", fill="#888", font=font(18))
button(d, (40, 280, 200, 325), "Log in", "#2563eb"); button(d, (220, 280, 440, 325), "Subscribe now", "#16a34a")
im.save(os.path.join(D, "ui22_login_wall.png"))

# dashboard (no login wall)
im, d = canvas("https://app.example.com/dashboard")
d.text((40, 55), "Dashboard", fill="black", font=font(34))
for i, (t, v) in enumerate([("Revenue", "$48,210"), ("Orders", "1,204"), ("Visitors", "9,873")]):
    x = 40 + i * 250; d.rectangle([x, 120, x + 220, 220], outline="#999")
    d.text((x + 14, 130), t, fill="#555", font=font(18)); d.text((x + 14, 165), v, fill="black", font=font(32))
d.text((40, 260), "Recent activity", fill="black", font=font(22))
for i in range(4): d.text((40, 300 + i * 30), f"Order #{1200 + i} shipped to customer", fill="#333", font=font(18))
im.save(os.path.join(D, "ui22_dashboard.png"))

# error page
im, d = canvas("https://shop.example.com/checkout")
d.text((40, 80), "500", fill="#dc2626", font=font(90))
d.text((40, 200), "Something went wrong", fill="black", font=font(32))
d.text((40, 250), "Internal Server Error. Please try again later.", fill="#444", font=font(20))
button(d, (40, 310, 200, 355), "Retry", "#2563eb")
im.save(os.path.join(D, "ui22_error_500.png"))

# cookie modal
im, d = canvas("https://blog.example.com")
d.text((40, 60), "Ten tips for faster builds", fill="#999", font=font(30))
d.rectangle([150, 140, 650, 380], fill="#f3f4f6", outline="#333", width=2)
d.text((175, 160), "We value your privacy", fill="black", font=font(26))
d.text((175, 205), "We use cookies to improve your experience.", fill="#333", font=font(18))
button(d, (175, 300, 345, 350), "Accept all", "#16a34a"); button(d, (365, 300, 625, 350), "Reject non-essential", "#6b7280")
im.save(os.path.join(D, "ui22_cookie_modal.png"))

# destructive confirm dialog
im, d = canvas("https://cloud.example.com/projects/prod-db")
d.rectangle([150, 120, 650, 380], fill="#fff", outline="#dc2626", width=3)
d.text((175, 140), "Delete database prod-db?", fill="#dc2626", font=font(28))
d.text((175, 190), "This permanently deletes all data. This cannot be undone.", fill="#333", font=font(17))
button(d, (175, 300, 345, 350), "Cancel", "#6b7280"); button(d, (365, 300, 625, 350), "Delete permanently", "#dc2626")
im.save(os.path.join(D, "ui22_delete_confirm.png"))
