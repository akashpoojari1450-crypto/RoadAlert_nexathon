# RoadAlert / TrafficSense: Phase 1 backend

Django + Django REST Framework. Uses SQLite for local development with plain
latitude/longitude and haversine distance. For production, switch `DATABASES` to
PostgreSQL + PostGIS and replace the bounding-box lookups with PostGIS distance queries.

## Web dashboards (open in a browser)
    python manage.py migrate
    python manage.py seed_demo          # demo company, accounts, journeys, incidents (DEBUG only)
    python manage.py runserver
Then open http://127.0.0.1:8000/ and sign in:
| Page | Username | Password |
|---|---|---|
| Employer: employees' travel status, delay, verification, evidence | `demo_employer` | `demo12345` |
| Authority: live incidents with Confirm / Resolve, monthly report with hotspots | `demo_authority` | `demo12345` |

`seed_demo --reset` rebuilds the demo data. It refuses to run when `DEBUG` is off, because the
demo accounts use a known password. Delete them before any real deployment. The time zone is
`Asia/Kolkata` (change `TIME_ZONE` in `config/settings.py` if needed).

## Live simulation (watch the system work without a mobile app)
Simulated phones drive a road, hit a jam, report it, and the server verifies it and warns people.
They talk to the server over HTTP exactly as the future app will. Both dashboards refresh every 30 s.

    python manage.py seed_demo --reset --accounts-only     # clean accounts, no old data
    python manage.py runserver                             # terminal 1
    python manage.py simulate_live                         # terminal 2 (about 40 seconds)

Open the employer page at http://127.0.0.1:8000/ while it runs. If you renamed the demo accounts,
pass them: `--employer-user NAME --employer-pass PASSWORD` (and `--authority-user/--authority-pass`).
`--interval 0` runs it instantly. What you should see: three drivers stuck at Thokkotu report the
jam, confidence rises with each report, the authority confirms it, and one driver still approaching
is warned (drivers already in the jam are not, and the employer's alert counts only people still ahead).

## Run
    python -m venv .venv && source .venv/bin/activate
    pip install -r requirements.txt
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py test
    python manage.py runserver

Create employer, authority and admin accounts in Django admin (`/admin/`): set the
user's `role` (and `company` for employers). Public sign-up always creates a passenger.

## API (header `Authorization: Token <key>`)
| Method | URL | Who |
|---|---|---|
| POST | /api/auth/register/ | anyone (creates a passenger) |
| POST | /api/auth/login/ | anyone, returns a token |
| GET | /api/auth/me/ | self |
| GET/POST | /api/auth/consent/ | employee (tracking consent, revocable) |
| GET/PUT | /api/auth/policy/ | read: company members; set: employer |
| POST | /api/auth/employees/ | employer adds an employee |
| POST | /api/incidents/report/ | any user (JSON or multipart with photo/video/voice) |
| GET | /api/incidents/nearby/?lat=&lng=&radius_m= | any user |
| GET | /api/incidents/<id>/ | any user |
| POST | /api/incidents/<id>/confirm/ , /resolve/ | authority |
| GET | /api/incidents/reports/monthly/?year=&month= | authority |
| GET | /api/journeys/ | self |
| POST | /api/journeys/start/ | self |
| POST | /api/journeys/<id>/points/ | self (batch of GPS points) |
| POST | /api/journeys/<id>/end/ | self |
| GET | /api/employer/dashboard/ | employer |
| POST | /api/routes/plan/ | any user: candidate routes, incidents on each, recommended route, alert |
| POST | /api/routes/avoid/ | any user: the "Avoid This Route" button |
| POST | /api/notifications/devices/ | register an FCM token (DELETE on logout) |
| GET | /api/notifications/?unread=1 | self: inbox |
| POST | /api/notifications/<id>/read/ | self |

`POST /api/journeys/start/` also accepts `route_geometry` (`[[lng, lat], ...]`), the route
the user chose. It is what lets the server warn people whose route passes a new incident.

## Routing and push setup
- **Routing:** uses OSRM. Set `OSRM_URL` to your own server for production (the public demo
  server is for light testing only). With `ROADALERT_OFFLINE=1`, or when running tests, a
  built-in fake provider is used so no network is needed. Google Maps can be added by writing
  one more provider class in `routing/providers.py`.
- **Push:** by default alerts are saved to the inbox and logged. For real Firebase push,
  `pip install firebase-admin` and set `FCM_CREDENTIALS_FILE` to your service-account JSON.

## Alert rules (`notifications/services.py`)
- An incident triggers alerts once it is heavy/severe/closed, or reported by 3+ different users.
- Travellers with an active journey whose route passes within 250 m are alerted, once per incident,
  but only if they have not reached the incident yet (at least 300 m before it, judged from their latest
  GPS point). The people who reported it are skipped.
- Employers get a count only ("2 employees travelling through the affected route may be
  delayed"), never names, and only for employees whose journeys are shared under the consent rules.

## How verification works (`incidents/verification.py`)
Levels come from independent evidence, never from what a user claims:
1. a user reported it
2. the reporter's own GPS trail was at the spot
3. at least 3 different users reported it
4. GPS speed data shows people crawling there for 5+ minutes (also gives the delay)
5. photo or video attached
6. a traffic authority confirmed it

Thresholds are in `ROADALERT` in `config/settings.py`.

## Privacy rules (enforced in `journeys/views.py`)
A journey is shared with the employer only if the user is an employee, has given
consent, the company has a policy covering that journey kind, and it is not personal.
Revoking consent hides existing journeys immediately.

## Not built yet
Photo AI, summary tables, SOS, voice-to-text, ESP32 camera node (Phase 2), Flutter app.
