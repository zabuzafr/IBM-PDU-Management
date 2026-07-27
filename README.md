# IBM PDU Manager — supervision de PDU IBM DPI via SNMP

Interface de **gestion indépendante** pour PDU IBM : UI React, API FastAPI (SNMP v2c/v3), **graphiques temps réel**, **mesures par prise**, **découverte réseau**, **export CSV**, **endpoint Prometheus**, et déploiement Docker. CI/CD GitHub Actions inclus.

Matériel de référence : **IBM DPI C19 PDU+** (firmware IBM DPI V0212), validé sur équipement réel.

## Fonctionnalités
- Profil intégré **IBM-DPI** : auto-détection du nombre de prises, noms des prises, mesures globales (V/A/W) **et par prise** (courant, puissance)
- **Auto-détection du modèle** via le `sysObjectID` SNMP (corrige automatiquement un PDU enregistré avec le mauvais profil)
- Commandes **ON/OFF/CYCLE** par prise — uniquement pour les modèles *commutables* (les PDU DPI surveillées les refusent proprement, voir « Matériels pris en charge »)
- Mesures (tension, courant, puissance) + **graphes** (Recharts) en temps réel
- Historique JSONL côté serveur + **Export CSV**
- **Prometheus** `/metrics` (volts/amps/watts par PDU) — idéal Grafana
- **Découverte réseau** `/discover?cidr=192.168.1.0/24`
- Auth JWT simple (démo), **admin hashé (bcrypt)**, rôles viewer/operator
- Logs détaillés et mode `DEBUG`, endpoint `/health`
- Docker Compose (api + ui), CI (build), Release (push images vers GHCR)

## Démarrage (Docker)
```bash
docker compose up -d --build
# UI: http://localhost:5173  (login: admin / admin  ou  reader / reader)
# API santé: http://localhost:8000/health
# Prometheus: http://localhost:8000/metrics
```

## Logs & Debug
- `DEBUG=true` dans `docker-compose.yml` (service `api`) active les logs détaillés : chaque requête HTTP (méthode, chemin, statut, durée), tentatives de connexion, erreurs avec stack trace.
- Suivre les logs : `docker compose logs -f api` (backend) ou `docker compose logs -f ui` (frontend).
- `GET /health` (sans auth) : vérifie que l'API répond et affiche le mode (`mock`, `debug`).
- Côté navigateur : la console (F12) affiche les logs `[PDU-UI]` (appels API, erreurs réseau/HTTP). L'écran de connexion teste automatiquement `/health` et indique si l'API est joignable.

## Dépannage
- **"API injoignable" sur l'écran de connexion** : le backend ne tourne pas ou n'est pas accessible → `docker compose ps` puis `docker compose logs api`.
- **"Identifiants invalides"** : comptes de démo `admin`/`admin` (operator) et `reader`/`reader` (viewer).
- **"PDU injoignable en SNMP (timeout)"** : le PDU ne répond pas aux requêtes SNMP. Vérifiez dans l'ordre : (1) SNMP est activé sur le PDU (interface web du PDU), (2) la communauté `SNMP_COMMUNITY` dans `docker-compose.yml` correspond à celle du PDU (une mauvaise communauté = timeout, pas d'erreur explicite), (3) le port UDP/161 n'est pas filtré. Test rapide depuis l'hôte : `snmpget -v2c -c public -t 2 <IP_PDU> 1.3.6.1.2.1.1.1.0` (paquet `snmp` : `apt install snmp`).
- **`npm ci` échoue au build** : le `package-lock.json` doit être synchronisé avec `package.json` → `cd frontend && npm install` puis rebuild.
- **Erreur 500 à la connexion** : vérifier les logs API ; `bcrypt` doit rester figé en `4.0.1` (incompatibilité connue passlib 1.7.4 / bcrypt ≥ 5).

<<<<<<< HEAD
## Matériels et MIB pris en charge

| Profil | Matériel testé | MIB (versionnée dans `mibs/`) | Firmware validé | Type |
|---|---|---|---|---|
| **IBM-DPI** (défaut) | IBM DPI C19 PDU+ (6 × C19) | `mibs/IBM_DPI_0_91_Linux.mib` (arbre Powerware, `1.3.6.1.4.1.534.6.6.2`) | IBM DPI V0212.0001 | **Surveillée** : mesures globales + par prise ; prises **non commutables** via SNMP (la MIB n'expose aucun objet de commande ON/OFF — c'est une limite du firmware/matériel, pas de l'application) |
| IBM-42R8743 | — (profil historique, non validé) | arbre IBM `1.3.6.1.4.1.2.6.223.8` | — | Commutable si le firmware l'expose |

Dernière MIB intégrée : **`IBM_DPI_0_91_Linux.mib`** (profil construit et vérifié à partir de la MIB **et** d'un `snmpwalk` sur PDU réel — le nombre de prises est auto-détecté en sondant les load groups, car l'OID « nombre de sorties » du firmware renvoie le nombre de phases).

## Ajouter un nouveau modèle de PDU (évolutions futures)

La prise en charge d'un nouvel équipement se fait par ajout d'un profil dans `backend/models.yaml`. Pour qu'un modèle soit intégré **et validé**, il faut impérativement fournir :

1. **La MIB officielle de l'équipement** (fichier `.mib` / `.txt` du constructeur, correspondant à la version de firmware utilisée) — à déposer dans le dossier `mibs/` du dépôt. C'est la source des OIDs (mesures, prises, éventuelles commandes).
2. **Un `snmpwalk` complet de l'équipement réel**, indispensable pour vérifier que le firmware expose bien ce que la MIB annonce (les écarts sont fréquents : OIDs absents, unités, indexation) :
   ```bash
   # v2c
   snmpwalk -v2c -c <communauté> -t 2 <IP_PDU> 1.3.6.1.4.1 > walk_<modele>.txt
   # ou v3
   snmpwalk -v3 -l noAuthNoPriv -u <user> -t 2 <IP_PDU> 1.3.6.1.4.1 > walk_<modele>.txt
   ```
3. **Idéalement, un accès temporaire à un PDU de test** (prêt d'un équipement ou accès réseau à un exemplaire de labo) pour valider en conditions réelles : mesures, échelles/unités, et surtout les commandes ON/OFF/CYCLE si le modèle est commutable — celles-ci ne doivent **jamais** être validées à l'aveugle sur un équipement de production.

Sans ces éléments (au minimum MIB + snmpwalk), un profil ne peut être qu'approximatif et sera marqué comme *non validé*. Envoyez la MIB et le walk via une issue ou une pull request du dépôt, en précisant : modèle exact, version de firmware, nombre et type de prises, et si le PDU est commutable ou surveillé.
=======
## Profils PDU
- **IBM-DPI** (défaut) : firmware "IBM DPI" (arbre SNMP Powerware 534, cf. MIB IBM_DPI_0_91_Linux.mib). PDU **surveillée** : mesures globales (V/A/W/°C) et par prise (courant, puissance), mais **prises non commutables via SNMP** (la MIB ne contient aucun objet de commande ON/OFF).
- **IBM-42R8743** : ancien profil (arbre IBM 1.3.6.1.4.1.2.6.223.8), commutable si le firmware l'expose.
>>>>>>> 2b92c410788cd2d492efd5578c82d6a1c11d4499

## Passer en SNMP réel
- `USE_MOCK=false` dans `docker-compose.yml`
- v2c : `SNMP_VERSION=2c`, `SNMP_COMMUNITY=<community>`
- v3 : `SNMPV3_USER`, `SNMPV3_AUTH=SHA`, `SNMPV3_AUTH_KEY`, `SNMPV3_PRIV=AES`, `SNMPV3_PRIV_KEY`

## InfluxDB & Grafana (dashboards par prise)

Le `docker-compose.yml` inclut **InfluxDB 2.7** et **Grafana** préconfigurés :
- L'API collecte en **tâche de fond** (même GUI fermé) les mesures globales et **par prise** de chaque PDU toutes les `INFLUX_INTERVAL` secondes (30 s par défaut) et les écrit dans InfluxDB (mesures `pdu_metrics` et `pdu_outlet`, champ `estimated=1` quand la puissance est calculée V×I×cosφ).
- Grafana (http://localhost:3000, admin/admin) est provisionné automatiquement : datasource InfluxDB + dashboard **« PDU — consommation par prise »** (dossier PDU) avec la vue globale (W/A/V) et des **panneaux répétés pour chaque prise** (puissance et courant), sélection du PDU et des prises par variables.
- InfluxDB : http://localhost:8086 (admin/adminadmin, org `pdu`, bucket `pdu`, rétention 30 j).
- **Sécurité** : changez `INFLUX_TOKEN`, les mots de passe InfluxDB et Grafana avant toute mise en production.
- Pour désactiver l'export : retirez `INFLUX_URL`/`INFLUX_TOKEN` de l'environnement du service `api`.

## Renommage
- **PDU** : crayon sur la carte (GUI) ou `PUT /pdus/{id}` `{new_id, location?, notes?}` — l'historique de mesures suit le nouveau nom.
- **Prises** : crayon à côté du nom (GUI) ou `PUT /pdus/{id}/outlets/{idx}/name` `{name}` — le nom est **écrit dans le PDU** via SNMP (OID de nom en read-write sur le firmware DPI), il est donc visible par tous les outils.
- Ces opérations requièrent le rôle `operator`.

## API utile
- `GET /pdus` — liste des PDU
- `POST /pdus` — ajouter `{id, ip, model}` (model par défaut: IBM-DPI)
- `GET /pdus/{id}/outlets` — prises (nom + état)
- `POST /pdus/{id}/outlets/{idx}/action` body `{action:on|off|cycle}`
- `GET /pdus/{id}/metrics?record=true` — mesures + enregistrement historique
- `GET /pdus/{id}/metrics/history?limit=N&since=ISO&until=ISO`
- `GET /pdus/{id}/metrics/history.csv?...` — export CSV
- `GET /discover?cidr=192.168.1.0/24` — découverte SNMP basique
- `GET /metrics` — exposition Prometheus

## CI/CD
- `.github/workflows/ci.yml` : build backend/frontend + images Docker (sans push) à chaque push/PR
- `.github/workflows/release-docker.yml` : push multi-arch sur GHCR lors d’un tag `v*.*.*`

## Licence
MIT — testez en environnement isolé avant prod.
