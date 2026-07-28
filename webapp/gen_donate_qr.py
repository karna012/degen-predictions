"""One-off script: generates the static QR code PNGs for the donation
modal (static/donate/*.png). Run once whenever an address changes;
not part of the request-serving path."""
import os

import qrcode

ADDRESSES = {
    "btc": "bc1qlyx4dkcmlkex3ynh6wh5uvxer49eg6pr4s9ndk",
    "eth": "0x59991B5209DAc03005376Ef23298D23e06460Cbc",
    "bnb": "0x59991B5209DAc03005376Ef23298D23e06460Cbc",
    "usdt": "0x59991B5209DAc03005376Ef23298D23e06460Cbc",
    "usdc": "0x59991B5209DAc03005376Ef23298D23e06460Cbc",
    "sol": "Cfn6hQfCMSF3WEr82WjpHRGUV8bYUuoQPPqdUwHqzM8s",
}

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "donate")
os.makedirs(OUT_DIR, exist_ok=True)

for key, addr in ADDRESSES.items():
    img = qrcode.make(addr, box_size=8, border=2)
    path = os.path.join(OUT_DIR, f"{key}.png")
    img.save(path)
    print(f"{key}: saved {path}")
