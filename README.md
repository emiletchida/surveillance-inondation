# Surveillance d'inondation — Québec

Système automatisé de surveillance du risque d'inondation, avec deux capteurs
complémentaires et alertes par courriel.

## Ce que fait le système

**Capteur fluvial** — surveille l'état officiel des stations hydrométriques d'un
bassin versant via la plateforme Vigilance du ministère de la Sécurité publique
du Québec. Détecte les entrées en surveillance et les aggravations.

**Capteur pluvial** — balaie toutes les alertes météo actives du Québec via
l'API GeoMet d'Environnement et Changement climatique Canada, et isole celles
de nature pluviale (pluie, orages, averses, inondation).

Quand une alerte se déclenche, un courriel est envoyé automatiquement.

## Sources de données

Toutes publiques, gratuites, sans clé d'API :

- Vigilance (MSP Québec) — état et niveau des stations
- GeoMet-OGC (ECCC) — alertes météorologiques actives

## Configuration

Trois secrets à définir dans le dépôt (Settings → Secrets and variables → Actions) :

| Secret | Contenu |
|---|---|
| `GMAIL_ADRESSE` | adresse Gmail d'envoi |
| `GMAIL_MOT_DE_PASSE` | mot de passe d'application Google (16 caractères) |
| `GMAIL_DESTINATAIRE` | adresse qui reçoit les alertes |

## Exécution

Le workflow s'exécute automatiquement chaque heure. Il peut aussi être lancé
manuellement depuis l'onglet Actions.

## Avertissement

Ce système est un outil d'information complémentaire. Il ne remplace en aucun cas
les avertissements officiels des autorités. En cas d'urgence, référez-vous à
Vigilance (https://vigilance.gouv.qc.ca) et aux consignes de la sécurité civile.
