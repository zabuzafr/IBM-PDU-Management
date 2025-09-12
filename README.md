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
docker compose up -d
# UI: http://localhost:5173  (login: admin / admin)
# Prometheus: http://localhost:8000/metrics
```

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
