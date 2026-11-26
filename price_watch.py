"""
Fred Perry Preis-Wächter
Durchsucht Google Shopping Deutschland (über SerpApi) nach Fred Perry
Poloshirts und schickt eine E-Mail, sobald ein Angebot unter dem
Maximalpreis auftaucht. Jedes Angebot wird nur einmal gemeldet.

Benötigte GitHub-Secrets:
  SERPAPI_KEY         - API-Schlüssel von serpapi.com
  GMAIL_ADRESSE       - Gmail-Adresse, von der verschickt wird
  GMAIL_APP_PASSWORT  - App-Passwort dieser Gmail-Adresse
  EMPFAENGER          - E-Mail-Adresse, die die Info bekommt
"""

import json
import os
import smtplib
import sys
from email.message import EmailMessage
from pathlib import Path

import requests
from requests.utils import requote_uri
from urllib.parse import quote_plus

# ---------------- Einstellungen ----------------
SUCHBEGRIFF = "Fred Perry Polo Shirt Herren"
MAX_PREIS = 76.0

# Diese Wörter MÜSSEN im Titel vorkommen
PFLICHT_WOERTER = ["fred perry", "polo"]
# Angebote mit diesen Wörtern im Titel werden ignoriert
AUSSCHLUSS_WOERTER = ["kinder", "kids", "junior", "baby", "gebraucht", "used", "pre-owned"]

GESEHEN_DATEI = Path("gesehene_angebote.json")

# Für so viele Angebote pro Mail wird der direkte Shop-Link abgefragt.
# Jede Abfrage kostet eine zusätzliche SerpApi-Suche (100 frei pro Monat).
MAX_DIREKTLINKS = 3
# ------------------------------------------------


def secret(name):
    """Liest ein Secret und gibt eine verständliche Meldung, falls es fehlt."""
    wert = (os.getenv(name) or "").strip()
    if not wert:
        print(f"FEHLER: Das Secret '{name}' fehlt oder ist leer. "
              f"Bitte unter Settings -> Secrets and variables -> Actions anlegen.")
        sys.exit(1)
    return wert


def suche_angebote(api_key):
    params = {
        "engine": "google_shopping",
        "q": SUCHBEGRIFF,
        "gl": "de",
        "hl": "de",
        "google_domain": "google.de",
        "api_key": api_key,
    }
    antwort = requests.get("https://serpapi.com/search.json", params=params, timeout=60)
    daten = antwort.json()
    if "error" in daten:
        print(f"FEHLER von SerpApi: {daten['error']}")
        sys.exit(1)
    return daten.get("shopping_results", []) + daten.get("inline_shopping_results", [])


def ist_passend(angebot):
    titel = (angebot.get("title") or "").lower()
    preis = angebot.get("extracted_price")
    if preis is None or preis >= MAX_PREIS:
        return False
    if not all(w in titel for w in PFLICHT_WOERTER):
        return False
    if any(w in titel for w in AUSSCHLUSS_WOERTER):
        return False
    if angebot.get("second_hand_condition"):
        return False
    return True


def angebot_id(angebot):
    return f"{angebot.get('title')}|{angebot.get('source')}|{angebot.get('extracted_price')}"


def lade_gesehene():
    if GESEHEN_DATEI.exists():
        try:
            return set(json.loads(GESEHEN_DATEI.read_text(encoding="utf-8")))
        except Exception:
            return set()
    return set()


def speichere_gesehene(ids):
    GESEHEN_DATEI.write_text(json.dumps(sorted(ids), ensure_ascii=False, indent=2), encoding="utf-8")


def mail_verschicken(betreff, text, absender, passwort, empfaenger):
    msg = EmailMessage()
    msg["Subject"] = betreff
    msg["From"] = absender
    msg["To"] = empfaenger
    msg.set_content(text)
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
            smtp.login(absender, passwort)
            smtp.send_message(msg)
    except smtplib.SMTPAuthenticationError:
        print("FEHLER: Gmail hat die Anmeldung abgelehnt. Prüfe GMAIL_ADRESSE und "
              "GMAIL_APP_PASSWORT (muss ein App-Passwort sein, nicht dein normales Passwort).")
        sys.exit(1)
    print(f"E-Mail an {empfaenger} verschickt: {betreff}")


def shop_link_abfragen(angebot, api_key):
    """Fragt bei SerpApi die Händlerliste des Produkts ab und sucht den direkten Shop-Link."""
    token = angebot.get("immersive_product_page_token")
    if not token:
        return None
    try:
        r = requests.get("https://serpapi.com/search.json", timeout=60, params={
            "engine": "google_immersive_product",
            "page_token": token,
            "gl": "de",
            "hl": "de",
            "api_key": api_key,
        })
        stores = r.json().get("product_results", {}).get("stores", [])
    except Exception as e:
        print(f"  Hinweis: Direkter Link für '{angebot.get('title')}' nicht abrufbar ({e})")
        return None

    shop = (angebot.get("source") or "").lower()
    # Zuerst den Shop nehmen, der den gefundenen Preis anbietet
    for st in stores:
        if st.get("link") and shop and shop in (st.get("name") or "").lower():
            return st["link"]
    for st in stores:
        if st.get("link"):
            return st["link"]
    return None


def bester_link(angebot, api_key, direkt_erlaubt):
    if direkt_erlaubt:
        direkt = shop_link_abfragen(angebot, api_key)
        if direkt:
            return requote_uri(direkt)
    if angebot.get("link"):
        return requote_uri(angebot["link"])
    if angebot.get("product_link"):
        return requote_uri(angebot["product_link"])  # Leerzeichen werden zu %20
    # Notlösung: Google-Suche nach Titel + Shop
    return "https://www.google.de/search?q=" + quote_plus(f"{angebot.get('title')} {angebot.get('source')}")


def angebots_zeilen(angebote, api_key):
    zeilen = []
    for i, a in enumerate(angebote):
        link = bester_link(a, api_key, direkt_erlaubt=i < MAX_DIREKTLINKS)
        zeilen.append(f"• {a.get('title')}\n  {a.get('price')} bei {a.get('source')}\n  {link}\n")
    return zeilen


def sende_testmail(alle, api_key, absender, passwort, empfaenger):
    fp = [a for a in alle
          if "fred perry" in (a.get("title") or "").lower() and a.get("extracted_price") is not None]
    fp.sort(key=lambda a: a["extracted_price"])
    zeilen = ["Das ist eine Testmail von deinem Fred Perry Preis-Wächter. Der Mailversand funktioniert! 🎉\n",
              f"Aktuell gefunden: {len(alle)} Ergebnisse, davon {len(fp)} von Fred Perry.",
              f"Gemeldet wird automatisch alles unter {MAX_PREIS:.0f} €.\n",
              "Die 5 günstigsten Fred Perry Angebote gerade (egal welcher Preis):\n"]
    zeilen += angebots_zeilen(fp[:5], api_key) or ["(keine gefunden)"]
    mail_verschicken("Testmail: Fred Perry Preis-Wächter", "\n".join(zeilen), absender, passwort, empfaenger)


def sende_mail(angebote, api_key, absender, passwort, empfaenger):
    zeilen = [f"Es gibt {len(angebote)} neue Fred Perry Polo-Angebote unter {MAX_PREIS:.0f} €:\n"]
    zeilen += angebots_zeilen(angebote, api_key)
    zeilen.append("Tipp: Bei sehr günstigen Preisen von unbekannten Shops auf Fake-Shops achten "
                  "(Impressum, Bewertungen, keine Vorkasse).")

    mail_verschicken(f"Fred Perry Polo unter {MAX_PREIS:.0f} € gefunden!", "\n".join(zeilen),
                     absender, passwort, empfaenger)


def main():
    api_key = secret("SERPAPI_KEY")
    absender = secret("GMAIL_ADRESSE")
    passwort = secret("GMAIL_APP_PASSWORT").replace(" ", "")  # Leerzeichen aus App-Passwort entfernen
    empfaenger = secret("EMPFAENGER")

    gesehene = lade_gesehene()
    alle = suche_angebote(api_key)
    treffer = [a for a in alle if ist_passend(a)]
    neue = [a for a in treffer if angebot_id(a) not in gesehene]

    print(f"{len(alle)} Suchergebnisse insgesamt.")
    print("Günstigste Ergebnisse:")
    for a in sorted([a for a in alle if a.get("extracted_price") is not None],
                    key=lambda a: a["extracted_price"])[:5]:
        print(f"  {a.get('price')}  {a.get('title')}  ({a.get('source')})")
    print(f"{len(treffer)} passende Angebote unter {MAX_PREIS:.0f} € gefunden, davon {len(neue)} neu.")

    if os.getenv("TESTMAIL", "").lower() == "true":
        sende_testmail(alle, api_key, absender, passwort, empfaenger)

    if neue:
        neue.sort(key=lambda a: a["extracted_price"])
        sende_mail(neue, api_key, absender, passwort, empfaenger)
        gesehene.update(angebot_id(a) for a in neue)

    speichere_gesehene(gesehene)


if __name__ == "__main__":
    main()
