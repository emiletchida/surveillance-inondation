# -*- coding: utf-8 -*-
"""
SURVEILLANCE D'INONDATION - version GitHub Actions
Deux capteurs : FLUVIAL (Vigilance MSP) + PLUVIAL (alertes ECCC, tout le Quebec)
Envoie un courriel UNIQUEMENT pour les evenements serieux ET nouveaux.

AMELIORATIONS (anti-spam) :
  1. SEVERITE : le capteur pluvial ne retient que les AVERTISSEMENTS officiels
     (pas les veilles ni les bulletins speciaux, qui sont mineurs).
  2. MEMOIRE : chaque evenement n'est signale QU'UNE FOIS. Tant qu'une alerte
     reste active, elle n'est pas renotifiee a chaque execution.

Identifiants Gmail lus depuis les Secrets GitHub. Heures a l'heure du Quebec.

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
#  REGLAGES DE SENSIBILITE (pluvial)
# ============================================================
# Niveau minimal retenu :
#   "avertissement" = seulement les avertissements confirmes (recommande)
#   "tout"          = inclut aussi les veilles et bulletins (bcp plus de mails)
NIVEAU_SEVERITE = "avertissement"

# Pour ne garder QUE les evenements extremes, decommente la ligne suivante :
# COULEURS_EXTREMES = ["orange", "rouge"]
COULEURS_EXTREMES = []          # [] = toutes les couleurs

# ============================================================
#  IDENTIFIANTS - lus depuis les Secrets GitHub
# ============================================================
COURRIEL_EXPEDITEUR = os.environ.get("GMAIL_ADRESSE", "")
MOT_DE_PASSE_APPLI = os.environ.get("GMAIL_MOT_DE_PASSE", "")
COURRIEL_DESTINATAIRE = os.environ.get("GMAIL_DESTINATAIRE", "")

BASSIN_FLUVIAL = "Chaudière"

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
#  CAPTEUR 2 - PLUVIAL (tout le Quebec, avertissements seulement)
# ============================================================
def est_pluviale_extreme(p):
    """Vrai si l'alerte est pluviale ET assez serieuse pour notifier."""
    nom = (p.get("alert_name_fr", "") + " " +
           p.get("alert_short_name_fr", "")).lower()
    if not any(m in nom for m in MOTS_PLUVIAL):
        return False
    # Filtre de severite : avertissements seulement (pas veilles/bulletins)
    if NIVEAU_SEVERITE == "avertissement" and p.get("alert_type", "") != "warning":
        return False
    # Filtre de couleur (optionnel)
    if COULEURS_EXTREMES and p.get("risk_colour_fr", "") not in COULEURS_EXTREMES:
        return False
    return True


def alertes_pluviales_quebec():
    resp = requests.get(URL_ALERTES, params={
        "f": "json", "bbox": BBOX_QUEBEC, "limit": 1000}, timeout=30)
    resp.raise_for_status()
    resultats, vus = [], set()
    for f in resp.json().get("features", []):
        p = f.get("properties", {})
        if p.get("province", "") != "QC":
            continue
        if not est_pluviale_extreme(p):
            continue
        cle = "%s|%s" % (p.get("feature_name_fr", ""), p.get("alert_name_fr", ""))
        if cle in vus:
            continue
        vus.add(cle)
        resultats.append({
            "cle": cle,
            "zone": p.get("feature_name_fr", "?"),
            "alerte": p.get("alert_name_fr", "?"),
            "couleur": p.get("risk_colour_fr", ""),
        })
    return resultats


# ============================================================
#  ETAT PERSISTANT (fluvial + pluvial dans un seul fichier)
# ============================================================
def charger_etat():
    """
    Retourne (etat_fluvial, cles_pluviales_connues, pluvial_deja_initialise).
    Gere l'ancien format (dict plat = fluvial seulement).
    """
    if os.path.exists(FICHIER_ETAT):
        with open(FICHIER_ETAT, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and "fluvial" in data:
            return data.get("fluvial", {}), set(data.get("pluvial", [])), True
        # ancien format : dict plat station->etat
        return data, set(), False
    return {}, set(), False


def sauver_etat(stations_fluv, cles_pluviales):
    data = {
        "fluvial": {sid: s["etat"] for sid, s in stations_fluv.items()},
        "pluvial": sorted(cles_pluviales),
    }
    with open(FICHIER_ETAT, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ============================================================
#  PROGRAMME PRINCIPAL
# ============================================================
def main():
    journaliser("=== Surveillance d'inondation ===")
    messages = []
    etat_fluvial, cles_pluv_avant, pluvial_initialise = charger_etat()
    stations = {}

    # --- Capteur fluvial ---
    try:
        stations = charger_stations_fluviales(BASSIN_FLUVIAL)
        premiere_fois = (len(etat_fluvial) == 0)
        alertes = analyser_fluvial(stations, etat_fluvial)
        journaliser("Fluvial (%s) : %d stations, %d alerte(s)." % (
            BASSIN_FLUVIAL, len(stations), 0 if premiere_fois else len(alertes)))
        if not premiere_fois:
            for type_a, s, avant in alertes:
                m = "FLUVIAL - %s : %s (%s) -> %s [%s m]" % (
                    type_a, s["id"], s["lieu"], s["etat"], s["niveau_m"] or "n/d")
                journaliser(">> " + m)
                messages.append(m)
        else:
            journaliser("Premiere execution fluviale : etat de reference enregistre.")
    except Exception as e:
        journaliser("Erreur capteur fluvial : %s" % e)

    # --- Capteur pluvial (avertissements seulement, avec memoire) ---
    cles_pluv_actuelles = set()
    try:
        actuelles = alertes_pluviales_quebec()
        cles_pluv_actuelles = {a["cle"] for a in actuelles}
        journaliser("Pluvial (Quebec) : %d avertissement(s) actif(s)." % len(actuelles))
        if not pluvial_initialise:
            journaliser("Premiere execution pluviale : reference enregistree (aucun courriel).")
        else:
            nouvelles = [a for a in actuelles if a["cle"] not in cles_pluv_avant]
            journaliser("Pluvial : %d NOUVEL(LE)(S) avertissement(s)." % len(nouvelles))
            for a in nouvelles:
                coul = (" [%s]" % a["couleur"]) if a["couleur"] else ""
                m = "PLUVIAL - %s : %s%s" % (a["zone"], a["alerte"], coul)
                journaliser(">> " + m)
                messages.append(m)
    except Exception as e:
        journaliser("Erreur capteur pluvial : %s" % e)

    # --- Sauvegarde de l'etat (fluvial + pluvial) ---
    try:
        sauver_etat(stations, cles_pluv_actuelles)
    except Exception as e:
        journaliser("Erreur sauvegarde etat : %s" % e)

    # --- Envoi du courriel (seulement si nouveautes) ---
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
        journaliser("Aucune nouveaute. Aucun courriel envoye.")


if __name__ == "__main__":
    main()
