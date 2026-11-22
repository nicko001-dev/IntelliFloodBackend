# IntelliFlood — Backend (FastAPI)

Backend du système intelligent de prévention et de gestion des inondations en Côte d'Ivoire (prototype d'alerte précoce à Abidjan).

```
Capteurs ESP32 ──HTTP/MQTT──▶  API FastAPI  ──▶ Seuils (vert/orange/rouge) ──▶ IA (rédaction) ──▶ SMS / Push / ONPC
 (ou simulateur)                    │                                                                  │
                                    ▼                                                                  ▼
                            Base de données  ◀──── lecture ────  Application Flutter (carte, alertes, détail, autorités)
                                                      + WebSocket /ws (mises à jour en direct)
```

Principe clé du cahier des charges : **le niveau d'alerte est décidé uniquement par les seuils, jamais par l'IA**. L'IA ne fait que rédiger les messages, et sa sortie est validée (longueur, chiffres, pas d'ordre d'évacuation) avant usage ; sinon un message prédéfini est utilisé.

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate      # Windows : .venv\Scripts\activate
pip install -r requirements-dev.txt
cp .env.example .env                                    # puis adapter (au minimum ADMIN_API_KEY)
python -m scripts.seed                                  # zones d'Abidjan + 4 points de démo (note les clés d'API !)
uvicorn app.main:app --reload
```

- Documentation interactive : <http://localhost:8000/docs>
- Par défaut : SQLite, SMS **simulés** (affichés dans les logs), pas d'IA (messages prédéfinis) → tout fonctionne sans compte externe.

### Démonstration « l'eau monte, l'obstacle apparaît, l'alerte part, la carte change »

```bash
# 1) inscrire un numéro (zone 1 = Riviera, Cocody)
curl -X POST localhost:8000/api/v1/subscriptions -H 'content-type: application/json' \
     -d '{"phone":"0707123456","zone_id":1}'
# 2) lancer le simulateur de capteur avec la clé de COC-001 affichée par le seed
python -m scripts.simulate --api-key if_xxx --point COC-001 --scenario demo
```

Scénarios : `stable`, `slow_rise`, `fast_rise`, `obstruction`, `demo`. Le délai mesure → alerte est mesuré automatiquement : `GET /api/v1/admin/stats/latency`.

### Docker (PostgreSQL)

```bash
cp .env.example .env
docker compose up --build            # API sur :8000 avec PostgreSQL
docker compose --profile mqtt up     # + broker MQTT Mosquitto (dev)
docker compose exec api python -m scripts.seed
```

## Authentification

| Qui | En-tête | Accès |
|---|---|---|
| Capteur (ESP32) | `X-API-Key` (clé propre au point, affichée **une seule fois** à la création ; ou `SENSOR_API_KEY` globale) | `POST /measurements` |
| Autorités / admin | `X-Admin-Key` (= `ADMIN_API_KEY`) | `/authorities/*`, `/admin/*` |
| Application mobile | aucune (lecture publique, écritures limitées en débit) | le reste |

Les clés de capteur sont stockées hachées (SHA-256). En production, `ADMIN_API_KEY` (≥ 16 caractères) et `CORS_ORIGINS` sont obligatoires, sinon le serveur refuse de démarrer.

## Endpoints principaux (préfixe `/api/v1`)

**Capteurs** — `POST /measurements`, `POST /measurements/batch` (rattrapage après coupure réseau)

```json
{ "point_code": "COC-001",
  "readings_cm": [70.1, 70.4, 69.8, 250.0, 70.2],   // lectures brutes : le serveur filtre (F1.2)
  "distance_aval_cm": 95.0,                            // optionnel : 2e capteur, détection de blocage (F2.3)
  "obstruction_distance_cm": 88.0,                     // optionnel : lecture dédiée à l'obstruction (F2.2)
  "measured_at": "2026-11-22T10:00:00Z" }              // optionnel (heure serveur par défaut)
```
Au lieu de `readings_cm`, on peut envoyer `distance_cm` déjà filtré par l'ESP32. Réponse : niveau d'alerte, raisons, `alert_id` si une alerte vient d'être déclenchée.

**Application mobile** (écrans F6.x)

| Écran | Endpoint |
|---|---|
| Accueil | `GET /points`, `GET /stats/summary` |
| Carte | `GET /points/geojson` (Leaflet / `flutter_map`) |
| Alertes | `GET /alerts?status=&level=&zone_id=` |
| Détail d'un point | `GET /points/{id}`, `/points/{id}/history?hours=24&bucket_minutes=10`, `/points/{id}/measurements` |
| Inscription | `GET /zones`, `POST /subscriptions`, `PATCH/DELETE /subscriptions/{token}`, `POST /devices` (jeton FCM) |
| Signalement (optionnel) | `POST /reports`, `GET /reports` (signalements vérifiés) |
| Temps réel (F6.7) | WebSocket `/ws` → événements `measurement`, `alert_created`, `alert_updated` |

**Autorités** — `GET /authorities/dashboard` : tous les points classés par gravité (niveau, obstruction, vitesse de montée), compteurs, alertes ouvertes, capteurs hors ligne.

**Administration** — zones, points (seuils configurables, `calibrate` pour D0, `rotate-key`), clôture d'alerte, modération des signalements, journal des notifications, `POST /admin/ai/preview` (tester les messages IA sur des scénarios), `POST /admin/notifications/test-sms`, `GET /admin/stats/latency`.

## Règles métier (cahier des charges §5–6)

| Élément | Règle |
|---|---|
| Hauteur d'eau | `sensor_height_cm − distance mesurée` (≥ 0) ; en % de la hauteur utile du point |
| Niveau d'eau | vert < 50 % · orange de 50 à 80 % **ou montée rapide** · rouge > 80 % |
| Montée rapide | pente (cm/min, régression sur 5 min) ≥ `fast_rise_cm_min` (2 par défaut) **et** niveau ≥ 10 % (anti-bruit) |
| Obstruction | `(D0 − D) / hauteur utile × 100` → libre < 20 · partielle 20–50 · forte 50–80 · critique > 80 |
| Blocage amont/aval | niveau amont − niveau aval ≥ `blockage_delta_cm` → obstruction au moins « partielle » |
| Règle combinée | obstruction forte/critique + eau orange ⇒ **rouge** |
| Alerte | créée à l'escalade, résolue au retour au vert ; anti-spam (cooldown 5 min) |
| Diffusion | **Rouge** : SMS aux inscrits du quartier + push + message ONPC · **Orange** : push (SMS/ONPC activables) |

Tous les seuils sont **illustratifs et configurables par point** (aucun seuil officiel public n'existe, cf. cahier des charges).

## Fournisseurs externes (tous optionnels)

- **IA** : `AI_PROVIDER=anthropic` (clé `AI_API_KEY`) ou `openai_compatible` (Ollama/LM Studio en local et gratuit, Groq, Mistral…). Prompt strict, sortie JSON validée, repli automatique.
- **SMS** : `console` (simulation), `twilio`, `africastalking`. **À vérifier tôt : la couverture de la Côte d'Ivoire du fournisseur choisi** et les numéros de test (`POST /admin/notifications/test-sms`). Le texte SMS est converti en alphabet GSM-7 pour rester à 160 caractères.
- **Push** : `PUSH_PROVIDER=fcm` (Firebase Cloud Messaging v1) ; côté Flutter, envoyer le jeton via `POST /devices`.
- **MQTT** : `MQTT_ENABLED=true`, topic `intelliflood/points/<code>/measurements`, même JSON que l'HTTP avec un champ `"api_key"`.
- **ONPC** : `ONPC_PHONE_NUMBERS` / `ONPC_EMAILS` (+ `SMTP_*`) ; sans SMTP l'e-mail est simulé.

Un exemple de firmware ESP32 (non testé sur matériel) est dans `docs/esp32_exemple.ino`.

## Tests

```bash
pytest -q        # 70 tests : logique métier, API de bout en bout, WebSocket, IA, SMS/push (mockés), MQTT
ruff check .
```

## Couverture du cahier des charges et du suivi des tâches

| Module du suivi | Couvert par le backend |
|---|---|
| 1. Mesure du niveau d'eau | Filtrage (F1.2), hauteur, vitesse de montée, seuils configurables, réception HTTP/MQTT. *Hors backend : montage, firmware (exemple fourni), tests physiques.* |
| 2. Obstruction | D0 + calibration, taux, classes, amont/aval, règle combinée. *Hors backend : montage avec obstacle.* |
| 3. Serveur et base | API complète, historique mesures/alertes, clé d'API, tests automatisés |
| 4. Messages IA | Prompt, validation anti-invention, 160 caractères, message de secours, décision par seuils, outil de test des scénarios |
| 5. SMS et notifications | Fournisseurs SMS, déclenchement au rouge, push FCM, ONPC SMS/e-mail |
| 6. Application et carte | Toutes les routes des écrans (voir tableau) — l'application Flutter reste à faire |
| 7. Intégration et tests | Simulateur de scénarios, cas limites (capteur hors ligne, lots différés), mesure du délai alerte |
| 0 et 8 (cadrage, documentation) | Hors périmètre du code |

## Limites connues et pistes

- **Numéros non vérifiés** : n'importe qui peut inscrire un numéro. Prévoir une vérification par code SMS (OTP) avant un usage réel.
- Limiteur de débit en mémoire (un seul processus) ; migrations de base via `create_all` (ajouter Alembic avant d'évoluer le schéma en production).
- Pas de tâche de fond qui crée une alerte « capteur hors ligne » : l'état `hors_ligne` est calculé à la lecture et visible sur le tableau de bord autorités.
- Le chemin PostgreSQL, Twilio, Africa's Talking, FCM et MQTT réel n'ont pas été exécutés contre les vrais services (tests avec simulations) : à valider avec vos comptes.
- Interface testée uniquement via l'API ; les écrans Flutter doivent suivre les routes ci-dessus.
