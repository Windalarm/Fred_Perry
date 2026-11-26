"""
Fred Perry Preis-Wächter
Durchsucht Google Shopping (über SerpApi) nach Fred Perry Poloshirts
und schickt eine E-Mail (und optional eine SMS), sobald ein Angebot
unter dem Maximalpreis auftaucht. Jedes Angebot wird nur einmal gemeldet.
"""

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests

# ---------------- Einstellungen ----------------
SUCHBEGRIFF = os.getenv("SUCHBEGRIFF", "Fred Perry Polo Shirt Herren")
MAX_PREIS = float(os.getenv("MAX_PREIS", "60"))

# Diese Wörter MÜSSEN im Titel vorkommen
PFLICHT_WOERTER = ["fred perry", "polo"]
# Angebote mit diesen Wörtern im Titel werden ignoriert
AUSSCHLUSS_WOERTER = ["kinder", "kids", "junior", "baby", "gebraucht", "used", "pre-owned"]

GESEHEN_DATEI = Path("gesehene_angebote.json")
# ------------------------------------------------


def suche_angebote():
    """Holt Angebote aus Google Shopping Deutschland über SerpApi."""
    params = {
        "engine": "google_shopping",
        "q": SUCHBEGRIFF,
        "gl": "de",
        "hl": "de",
        "google_domain": "google.de",
        "api_key": os.environ["SERPAPI_KEY"],
    }
    antwort = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    antwort.raise_for_status()
    daten = antwort.json()

    if "error" in daten:
        raise RuntimeError(f"SerpApi-Fehler: {daten['error']}")

    return daten.get("shopping_results", []) + daten.get("inline_shopping_results", [])


def ist_passend(angebot):
    titel = (angebot.get("title") or "").lower()
    preis = angebot.get("extracted_price")

    if preis is None or preis >= MAX_PREIS:
        return False
    if not all(wort in titel for wort in PFLICHT_WOERTER):
        return False
    if any(wort in titel for wort in AUSSCHLUSS_WOERTER):
        return False
    if angebot.get("second_hand_condition"):
        return False
    return True


def angebot_id(angebot):
    # Gleicher Artikel beim gleichen Shop mit neuem Preis wird erneut gemeldet
    return f"{angebot.get('title')}|{angebot.get('source')}|{angebot.get('extracted_price')}"


def lade_gesehene():
    if GESEHEN_DATEI.exists():
        return set(json.loads(GESEHEN_DATEI.read_text(encoding="utf-8")))
    return set()


def speichere_gesehene(ids):
    GESEHEN_DATEI.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def sende_mail(angebote):
    zeilen = [f"Es gibt {len(angebote)} neue Fred Perry Polo-Angebote unter {MAX_PREIS:.0f} €:\n"]
    for a in angebote:
        link = a.get("link") or a.get("product_link") or ""
        zeilen.append(f"• {a.get('title')}\n  {a.get('price')} bei {a.get('source')}\n  {link}\n")
    zeilen.append("Tipp: Bei sehr günstigen Preisen von unbekannten Shops auf Fake-Shops achten "
                  "(Impressum, Bewertungen, keine Vorkasse).")

    msg = EmailMessage()
    msg["Subject"] = f"👕 Fred Perry Polo unter {MAX_PREIS:.0f} € gefunden!"
    msg["From"] = os.environ["SMTP_USER"]
    msg["To"] = os.environ["MAIL_AN"]
    msg.set_content("\n".join(zeilen))

    server = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    port = int(os.getenv("SMTP_PORT", "465"))
    with smtplib.SMTP_SSL(server, port) as smtp:
        smtp.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORT"])
        smtp.send_message(msg)
    print("E-Mail verschickt.")


def sende_sms(angebote):
    """Optional: SMS über Twilio, nur wenn die Twilio-Secrets gesetzt sind."""
    sid = os.getenv("TWILIO_SID")
    token = os.getenv("TWILIO_TOKEN")
    von = os.getenv("TWILIO_VON")
    an = os.getenv("SMS_AN")
    if not all([sid, token, von, an]):
        return

    guenstigstes = min(angebote, key=lambda a: a["extracted_price"])
    text = (f"Fred Perry Polo ab {guenstigstes.get('price')} bei {guenstigstes.get('source')}. "
            f"{len(angebote)} neue(s) Angebot(e) - Details per Mail.")

    r = requests.post(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data={"From": von, "To": an, "Body": text},
        auth=(sid, token),
        timeout=30,
    )
    r.raise_for_status()
    print("SMS verschickt.")


def main():
    gesehene = lade_gesehene()
    treffer = [a for a in suche_angebote() if ist_passend(a)]
    neue = [a for a in treffer if angebot_id(a) not in gesehene]

    print(f"{len(treffer)} passende Angebote, davon {len(neue)} neu.")

    if neue:
        neue.sort(key=lambda a: a["extracted_price"])
        sende_mail(neue)
        sende_sms(neue)
        gesehene.update(angebot_id(a) for a in neue)

    speichere_gesehene(gesehene)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"Fehler: {e}")
        sys.exit(1)
