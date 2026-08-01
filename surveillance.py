# -*- coding: utf-8 -*-
"""
SURVEILLANCE D'INONDATION - version GitHub Actions
Deux capteurs : FLUVIAL (Vigilance MSP) + PLUVIAL (alertes ECCC, tout le Quebec)
Envoie un courriel quand une alerte se declenche.

Les identifiants Gmail sont lus depuis les Secrets GitHub (jamais en clair).
Les heures sont affichees a l'heure du Quebec (ete/hiver gere automatiquement).

Sources (publiques, gratuites, verifiees) :
  - Vigilance MSP : etat officiel des stations
  - GeoMet ECCC   : alertes meteo actives (collection weather-alerts)
"""

import os
import csv
import io
import json
import smtplib
import ssl
import requests
from email.mime.text import MIMEText
from datetime import datetime
from zoneinfo import ZoneInfo

FUSEAU_QUEBEC = ZoneInfo("America/Toronto")

# ============================================================
#  INTERRUPTEUR DE TEST
#  True  = envoie un courriel de test et s'arrete (pour verifier
#          que l'envoi fonctionne depuis GitHub).
#  False = surveillance normale.
#  >>> Remets a False apres avoir confirme la reception. <<<
# ============================================================
MODE_TEST_COURRIEL = True

# ============================================================
#  IDENTIFIANTS - lus depuis les Secrets GitHub
# ============================================================
COURRIEL_EXPEDITEUR = os.environ.get("GMAIL_ADRESSE", "")
MOT_DE_PASSE_APPLI = os.environ.get("GMAIL_MOT_DE_PASSE", "")
COURRIEL_DESTINATAIRE = os.environ.get("GMAIL_DESTINATAIRE", "")

# ============================================================
#  ZONE SURVEILLEE
# ============================================================
BASSIN_FLUVIAL = "Chaudière"

# ============================================================
#  Constantes techniques (verifiees)
# ============================================================
URL_VIGILANCE = ("https://geoegl.msp.gouv.qc.ca/apis/mapserver-vigilance/ws/"
                 "vigilance.fcgi?service=wfs&version=1.1.0&request=getfeature"
                 "&typename=stations_igo2_public&outputformat=CSV")
URL_ALERTES = "https://api.weather.gc.ca/collections/weather-alerts/items"
BBOX_QUEBEC = "-80,44.9,-57,63"

NIVEAUX = {
    "État normal": 0, "État inconnu": 0, "Désactivée": 0,
    "En surveillance": 1, "Inondation mineure": 2,
    "Inondation moyenne": 3, "Inondation majeure": 4,
}
SEUIL_ALERTE = 1
MOTS_PLUVIAL = ["pluie", "inondation", "orage", "averse"]

FICHIER_ETAT = "etat_precedent.json"

SMTP_SERVEUR = "smtp.gmail.com"
SMTP_PORT = 587


def maintenant_quebec():
    return datetime.now(FUSEAU_QUEBEC).strftime("%Y-%m-%d %H:%M:%S")


def journaliser(message):
    print("[%s] %s" % (maintenant_quebec(), message))


# ============================================================
#  COURRIEL
# ============================================================
def envoyer_courriel(sujet, corps):
    if not (COURRIEL_EXPEDITEUR and MOT_DE_PASSE_APPLI and COURRIEL_DESTINATAIRE):
        journaliser("Identifiants courriel absents (verifie les Secrets GitHub).")
        return False
    msg = MIMEText(corps, "plain", "utf-8")
    msg["Subject"] = sujet
    msg["From"] = COURRIEL_EXPEDITEUR
    msg["To"] = COURRIEL_DESTINATAIRE
    contexte = ssl.create_default_context()
    with smtplib.SMTP(SMTP_SERVEUR, SMTP_PORT, timeout=30) as serveur:
        serveur.starttls(context=contexte)
        serveur.login(COURRIEL_EXPEDITEUR, MOT_DE_PASSE_APPLI)
        serveur.sendmail(COURRIEL_EXPEDITEUR, [COURRIEL_DESTINATAIRE],
                         msg.as_string())
    return True


# ============================================================
#  CAPTEUR 1 - FLUVIAL
# ============================================================
def charger_stations_fluviales(bassin):
    resp = requests.get(URL_VIGILANCE, timeout=30)
    resp.raise_for_status()
    resp.encoding = "utf-8"
    lecteur = csv.DictReader(io.StringIO(resp.text))
    stations = {}
    for r in lecteur:
        if bassin.lower() in r.get("plan_deau", "").lower():
            sid = r.get("station", "").strip()
            stations[sid] = {
                "id": sid,
                "lieu": r.get("description", "").strip(),
                "niveau_m": r.get("dern_valeur_niv", "").strip(),
                "etat": r.get("etat", "").strip(),
            }
    return stations


def analyser_fluvial(stations, etat_precedent):
    alertes = []
    for sid, s in stations.items():
        niv_actuel = NIVEAUX.get(s["etat"], 0)
        niv_avant = NIVEAUX.get(etat_precedent.get(sid), 0) if sid in etat_precedent else 0
        if niv_avant < SEUIL_ALERTE <= niv_actuel:
            alertes.append(("ENTREE EN RISQUE", s, etat_precedent.get(sid) or "(1re mesure)"))
        elif niv_actuel > niv_avant and niv_actuel >= SEUIL_ALERTE:
            alertes.append(("AGGRAVATION", s, etat_precedent.get(sid)))
    return alertes


# ============================================================
#  CAPTEUR 2 - PLUVIAL (tout le Quebec)
# ============================================================
def alertes_pluviales_quebec():
    resp = requests.get(URL_ALERTES, params={
        "f": "json", "bbox": BBOX_QUEBEC, "limit": 1000}, timeout=30)
    resp.raise_for_status()
    resultats, vus = [], set()
    for f in resp.json().get("features", []):
        p = f.get("properties", {})
        if p.get("province", "") != "QC":
            continue
        nom = (p.get("alert_name_fr", "") + " " +
               p.get("alert_short_name_fr", "")).lower()
        if not any(mot in nom for mot in MOTS_PLUVIAL):
            continue
        cle = (p.get("feature_name_fr", ""), p.get("alert_name_fr", ""))
        if cle in vus:
            continue
        vus.add(cle)
        resultats.append({
            "zone": p.get("feature_name_fr", "?"),
            "alerte": p.get("alert_name_fr", "?"),
            "couleur": p.get("risk_colour_fr", ""),
        })
    return resultats


# ============================================================
#  ETAT PERSISTANT
# ============================================================
def charger_etat():
    if os.path.exists(FICHIER_ETAT):
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            return json.load(f)
    return {}


def sauver_etat(stations):
    etat = {sid: s["etat"] for sid, s in stations.items()}
    with open(FICHIER_ETAT, "w", encoding="utf-8") as f:
        json.dump(etat, f, ensure_ascii=False, indent=2)


# ============================================================
#  PROGRAMME PRINCIPAL
# ============================================================
def main():
    # --- Mode test : envoie un courriel et s'arrete ---
    if MODE_TEST_COURRIEL:
        journaliser("MODE TEST : envoi d'un courriel de verification depuis GitHub...")
        try:
            ok = envoyer_courriel(
                "Test depuis GitHub - Surveillance d'inondation",
                "Ceci est un courriel de test envoye depuis GitHub Actions le %s "
                "(heure du Quebec).\n\nSi tu le recois, l'envoi automatique fonctionne "
                "parfaitement dans le nuage.\n\nRemets MODE_TEST_COURRIEL = False pour "
                "revenir a la surveillance normale." % maintenant_quebec())
            if ok:
                journaliser("Courriel de test envoye. Verifie ta boite Gmail.")
        except Exception as e:
            journaliser("ECHEC de l'envoi : %s" % e)
        return

    journaliser("=== Surveillance d'inondation ===")
    messages = []

    # --- Capteur fluvial ---
    try:
        stations = charger_stations_fluviales(BASSIN_FLUVIAL)
        etat_precedent = charger_etat()
        premiere_fois = (len(etat_precedent) == 0)
        alertes = analyser_fluvial(stations, etat_precedent)
        journaliser("Fluvial (%s) : %d stations, %d alerte(s)." % (
            BASSIN_FLUVIAL, len(stations), 0 if premiere_fois else len(alertes)))
        if not premiere_fois:
            for type_a, s, avant in alertes:
                m = "FLUVIAL - %s : %s (%s) -> %s [%s m]" % (
                    type_a, s["id"], s["lieu"], s["etat"], s["niveau_m"] or "n/d")
                journaliser(">> " + m)
                messages.append(m)
        else:
            journaliser("Premiere execution : etat de reference enregistre.")
        sauver_etat(stations)
    except Exception as e:
        journaliser("Erreur capteur fluvial : %s" % e)

    # --- Capteur pluvial ---
    try:
        pluviales = alertes_pluviales_quebec()
        journaliser("Pluvial (Quebec) : %d zone(s) en alerte." % len(pluviales))
        for a in pluviales:
            coul = (" [%s]" % a["couleur"]) if a["couleur"] else ""
            m = "PLUVIAL - %s : %s%s" % (a["zone"], a["alerte"], coul)
            journaliser(">> " + m)
            messages.append(m)
    except Exception as e:
        journaliser("Erreur capteur pluvial : %s" % e)

    # --- Envoi du courriel ---
    if messages:
        corps = ("Alerte(s) d'inondation detectee(s) le %s (heure du Quebec) :\n\n%s\n\n"
                 "Source officielle : https://vigilance.gouv.qc.ca") % (
            maintenant_quebec(),
            "\n".join("- " + m for m in messages))
        try:
            if envoyer_courriel("ALERTE inondation - %d evenement(s)" % len(messages),
                                corps):
                journaliser("Courriel envoye (%d evenement(s))." % len(messages))
        except Exception as e:
            journaliser("ECHEC envoi courriel : %s" % e)
    else:
        journaliser("Aucune alerte. Aucun courriel envoye.")


if __name__ == "__main__":
    main()
