# IBM PDU Manager (Regatta) — 42R8743 — PRO

Interface de **gestion indépendante** pour PDU IBM (profil **42R8743**) : UI React, API FastAPI (SNMP v2c/v3), **graphiques temps réel**, **découverte réseau**, **export CSV**, **endpoint Prometheus**, et déploiement Docker. CI/CD GitHub Actions inclus.

## Fonctionnalités
- Profil intégré **IBM-42R8743** (prises, états, noms via SNMP)
- Commandes **ON/OFF/CYCLE** par prise
- Mesures (tension, courant, puissance) + **graphes** (Recharts) en temps réel
- Historique JSONL côté serveur + **Export CSV**
- **Prometheus** `/metrics` (volts/amps/watts par PDU) — idéal Grafana
- **Découverte réseau** `/discover?cidr=192.168.1.0/24`
- Auth JWT simple (démo), **admin hashé (bcrypt)**, rôles viewer/operator
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

## Profils PDU
- **IBM-DPI** (défaut) : firmware "IBM DPI" (arbre SNMP Powerware 534, cf. MIB IBM_DPI_0_91_Linux.mib). PDU **surveillée** : mesures globales (V/A/W/°C) et par prise (courant, puissance), mais **prises non commutables via SNMP** (la MIB ne contient aucun objet de commande ON/OFF).
- **IBM-42R8743** : ancien profil (arbre IBM 1.3.6.1.4.1.2.6.223.8), commutable si le firmware l'expose.

## Passer en SNMP réel
- `USE_MOCK=false` dans `docker-compose.yml`
- v2c : `SNMP_VERSION=2c`, `SNMP_COMMUNITY=<community>`
- v3 : `SNMPV3_USER`, `SNMPV3_AUTH=SHA`, `SNMPV3_AUTH_KEY`, `SNMPV3_PRIV=AES`, `SNMPV3_PRIV_KEY`

## API utile
- `GET /pdus` — liste des PDU
- `POST /pdus` — ajouter `{id, ip, model}` (model par défaut: IBM-42R8743)
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
