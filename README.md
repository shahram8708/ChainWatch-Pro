# ⛓️ ChainWatch Pro

**AI-powered supply chain intelligence — track shipments, predict disruptions, and reroute before problems become crises.**

![Python](https://img.shields.io/badge/Python-3.11-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-latest-lightgrey?logo=flask)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-336791?logo=postgresql&logoColor=white)
![Redis](https://img.shields.io/badge/Redis-7-DC382D?logo=redis&logoColor=white)
![Celery](https://img.shields.io/badge/Celery-async-37814A?logo=celery&logoColor=white)
![Gemini](https://img.shields.io/badge/AI-Gemini%202.5%20Flash-4285F4?logo=google&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED?logo=docker&logoColor=white)

---

## 📋 Table of Contents

- [About the Project](#-about-the-project)
- [Key Features](#-key-features)
- [Tech Stack](#-tech-stack)
- [Project Structure](#-project-structure)
- [Getting Started](#-getting-started)
  - [Prerequisites](#prerequisites)
  - [Installation](#installation)
  - [Environment Variables](#environment-variables)
  - [Running the Project](#running-the-project)
- [Usage](#-usage)
- [API Documentation](#-api-documentation)
- [Configuration](#-configuration)
- [Testing](#-testing)
- [Deployment](#-deployment)
- [Contributing](#-contributing)
- [Roadmap](#-roadmap)
- [Acknowledgements](#-acknowledgements)

---

## 🧭 About the Project

Modern logistics teams drown in fragmented data — carrier portals, weather feeds, port congestion alerts, and WhatsApp updates from freight forwarders. By the time a disruption is visible, it's already too late to act.

**ChainWatch Pro** fixes that. It's a multi-tenant SaaS platform that pulls all of those signals together, computes a real-time **Disruption Risk Score (DRS)** for every active shipment, and uses Google Gemini AI to turn raw data into plain-English intelligence your operations team can actually act on.

This platform is aimed at logistics managers, supply chain directors, and operations teams at mid-sized companies moving freight by ocean, air, road, rail, or multimodal combinations — particularly businesses operating out of or into South/Southeast Asia and the Middle East. The INR-native billing via Razorpay and the built-in port coverage for INBOM, INNSA, SGSIN, AEDXB, and dozens more reflects a clear market focus.

What sets it apart is the depth of integration: it's not just a dashboard — it simulates disruption scenarios, generates AI route alternatives, monitors news and weather in the background, sends multi-channel alerts, and gives your C-suite an executive brief that refreshes every 12 hours. All of this behind a clean, Bootstrap-powered UI with full role-based access control and a superadmin panel for platform operators.

---

## ✨ Key Features

- **Disruption Risk Score Engine** — every shipment gets a 0–100 DRS calculated from carrier performance, weather, port congestion, news events, and transit delays; thresholds are Green / Watch / Warning / Critical.
- **AI-Powered Summaries** — Google Gemini 2.5 Flash generates plain-English disruption summaries, carrier performance commentaries, route risk assessments, and executive briefs, all with configurable TTL caching.
- **Route Optimizer** — automatically generates alternative carrier/route recommendations when a shipment's DRS crosses a threshold, with one-click approval or dismissal workflows.
- **Scenario Planner** — simulate a hypothetical shipment (origin, destination, mode, timing) and get an AI narrative on projected risk and booking strategy before you commit.
- **Live Risk Map** — visual map of all active shipments with DRS-coloured markers, clickable shipment panels, and real-time position interpolation between origin and destination.
- **Multi-Channel Alert System** — configurable rules fire alerts via email, SMS (Twilio), and outbound webhooks (including Slack) with HMAC-SHA256 signed payloads and automatic retries.
- **Carrier Intelligence** — track per-carrier on-time delivery rates, delay trends, and AI-generated performance commentary across all transport modes.
- **Executive Dashboard** — a board-ready dashboard with OTD trend, top lanes, top carriers, monthly spend, and a Gemini-generated daily brief.
- **Reports** — generate PDF and Excel reports (carrier performance, shipment summary, disruption analysis) with plan-gated usage limits and async background generation.
- **Bulk CSV Import** — upload shipments in bulk from a spreadsheet with template download, column validation, and row-level error reporting.
- **Full Audit Trail** — every meaningful action (shipment create/edit, alert acknowledge, recommendation approve, user invite) is recorded to a searchable audit log.
- **Multi-Tenant with RBAC** — organisations are completely isolated; users have one of four roles (superadmin, admin, manager, viewer) with route-level enforcement.
- **Razorpay Billing** — Starter, Professional, and Enterprise plans billed in INR with one-time order fallback when recurring plan IDs aren't configured.
- **Superadmin Panel** — a separately authenticated panel for platform operators to manage organisations, impersonate users, toggle feature flags, monitor Gemini API usage, and broadcast announcements.
- **Guided 4-Step Onboarding** — new organisations are walked through company profile, carrier setup, shipment creation, and notification configuration before hitting the main app.

---

## 🛠 Tech Stack

### Backend
| Technology | Purpose |
|---|---|
| Python 3.11 | Core language |
| Flask | Web framework (app factory pattern, blueprints) |
| Flask-SQLAlchemy | ORM |
| Flask-Migrate | Database migrations (Alembic) |
| Flask-Login | Session-based authentication |
| Flask-WTF / WTForms | Form handling and CSRF protection |
| Flask-Mail | SMTP email delivery |
| Flask-Limiter | Rate limiting (Redis-backed in production) |
| Gunicorn | WSGI production server |
| Celery | Async task queue and periodic beat scheduler |
| APScheduler | In-process scheduler fallback when Celery is disabled |
| marshmallow | Object serialization |

### AI & External APIs
| Technology | Purpose |
|---|---|
| Google Generative AI (`google-genai`) | Gemini 2.5 Flash for all AI content generation |
| OpenWeatherMap API | Weather data ingestion for port weather overlays |
| Twilio | Outbound SMS notifications |
| Razorpay | Payment processing and subscription management (INR) |

### Database & Cache
| Technology | Purpose |
|---|---|
| PostgreSQL 15 | Primary production database |
| SQLite | Development / test database |
| Redis 7 | Celery broker, rate limiter storage |

### Frontend
| Technology | Purpose |
|---|---|
| Jinja2 | Server-side HTML templating |
| Bootstrap 5 | UI component library |
| Custom CSS | `dashboard.css`, `main.css`, `public.css` |
| Vanilla JavaScript | `dashboard.js`, `map.js`, `charts.js`, `alerts.js`, `public.js` |
| Bootstrap Icons | Iconography throughout the UI |

### DevOps
| Technology | Purpose |
|---|---|
| Docker + Docker Compose | Containerised local and production deployment |
| WeasyPrint | Server-side PDF generation for reports |
| openpyxl | Excel report generation and CSV import processing |
| cryptography (Fernet) | Encrypted storage of carrier API credentials |
| bcrypt | Password hashing |
| python-dotenv | `.env` file loading |
| pip-audit | Dependency vulnerability scanning |

---

## 📁 Project Structure

```
ChainWatch-Pro-main/
├── run.py                          # App entrypoint; bootstraps DB on startup
├── config.py                       # Environment configs (Dev / Prod / Test)
├── celery_worker.py                # Celery app, all async tasks, beat schedule
├── requirements.txt                # Python dependencies
├── Dockerfile                      # Multi-stage Docker build (builder + runtime)
├── docker-compose.yml              # Orchestrates web, db, redis, celery_worker
├── .env.example                    # Template for all required environment variables
├── .gitignore
│
├── app/
│   ├── __init__.py                 # Flask application factory (create_app)
│   ├── extensions.py               # Shared Flask extensions (db, mail, login, etc.)
│   ├── commands.py                 # Flask CLI commands (superadmin seed, etc.)
│   │
│   ├── models/
│   │   ├── __init__.py             # Model registry exports
│   │   ├── user.py                 # User auth, roles, tokens, invitations
│   │   ├── organisation.py         # Tenant model, plan limits, subscription
│   │   ├── shipment.py             # Core shipment record (modes, ports, status)
│   │   ├── carrier.py              # Carrier reference data and SCAC codes
│   │   ├── carrier_performance.py  # Rolling OTD and delay statistics
│   │   ├── disruption_score.py     # Point-in-time DRS snapshots
│   │   ├── alert.py                # Alert records with severity and acknowledgement
│   │   ├── route_recommendation.py # AI-generated routing alternatives
│   │   ├── route_option.py         # Pre-seeded global route option library (large)
│   │   ├── ai_generated_content.py # Cached Gemini outputs keyed by type + context
│   │   ├── audit_log.py            # Immutable audit trail of all actions
│   │   ├── feature_flag.py         # Platform-wide feature toggles (superadmin)
│   │   ├── demo_lead.py            # Public demo request capture
│   │   └── types.py                # Custom SQLAlchemy column types (GUID, JSON)
│   │
│   ├── routes/
│   │   ├── __init__.py             # Blueprint registration
│   │   ├── auth.py                 # Login, register, verify, password reset
│   │   ├── dashboard.py            # Main operations dashboard
│   │   ├── executive.py            # Executive dashboard with AI brief
│   │   ├── shipments.py            # Shipment CRUD, detail, import, export
│   │   ├── alerts.py               # Alert list, detail, acknowledge
│   │   ├── carrier_intel.py        # Carrier performance intelligence view
│   │   ├── optimizer.py            # Route optimization UI
│   │   ├── planner.py              # Scenario planner / simulation UI
│   │   ├── risk_map.py             # Live shipment risk map
│   │   ├── reports.py              # Report generation and download
│   │   ├── audit.py                # Audit log viewer
│   │   ├── settings.py             # Profile, team, billing, alerts, integrations
│   │   ├── onboarding.py           # 4-step guided onboarding flow
│   │   ├── api.py                  # JSON API endpoints (/api/v1/*)
│   │   ├── webhooks.py             # Inbound Razorpay webhook handler
│   │   ├── superadmin.py           # Superadmin panel routes
│   │   └── public.py               # Public marketing pages (home, pricing, demo)
│   │
│   ├── services/
│   │   ├── ai_service.py           # Gemini API client, cache orchestration, schemas
│   │   ├── disruption_engine.py    # DRS computation logic for all shipments
│   │   ├── route_optimizer.py      # Alternative route generation engine
│   │   ├── simulation_service.py   # Scenario simulation (projected DRS, transit days)
│   │   ├── carrier_tracker.py      # Carrier position and status polling
│   │   ├── alert_service.py        # Alert rule evaluation and creation
│   │   ├── razorpay_service.py     # Billing, plan enforcement, webhook processing
│   │   ├── report_service.py       # PDF and Excel report builders (large file)
│   │   ├── team_import_service.py  # Bulk team member CSV import
│   │   ├── external_data/
│   │   │   ├── weather_service.py  # OpenWeatherMap integration
│   │   │   ├── port_data_service.py# Port congestion data aggregation
│   │   │   └── news_monitor_service.py # Gemini-powered route news scanning
│   │   └── notification/
│   │       ├── email_service.py    # Transactional email (alerts, invites, welcome)
│   │       ├── sms_service.py      # Twilio SMS for critical alerts
│   │       └── webhook_service.py  # Outbound webhook delivery with HMAC + retry
│   │
│   ├── forms/
│   │   ├── auth_forms.py           # Login, register, password forms
│   │   ├── shipment_forms.py       # Shipment create/edit form
│   │   ├── alert_forms.py          # Alert filter form
│   │   ├── settings_forms.py       # Profile, team, integrations settings forms
│   │   ├── onboarding_forms.py     # Multi-step onboarding forms
│   │   ├── optimizer_forms.py      # Route optimizer request form
│   │   └── simulation_forms.py     # Scenario planner input form
│   │
│   └── utils/
│       ├── decorators.py           # login_required, role_required, superadmin_required
│       ├── helpers.py              # format_inr, format_datetime_user, get_current_org
│       ├── pagination.py           # Pagination helper for list views
│       └── validators.py           # Custom WTForms validators
│
├── static/
│   ├── css/
│   │   ├── dashboard.css           # App dashboard styles
│   │   ├── main.css                # Global base styles
│   │   └── public.css              # Public marketing page styles
│   ├── js/
│   │   ├── dashboard.js            # Dashboard polling, DRS updates, modals
│   │   ├── map.js                  # Risk map rendering and shipment markers
│   │   ├── charts.js               # Chart components (OTD, DRS history, etc.)
│   │   ├── alerts.js               # Alert list interactions and polling
│   │   └── public.js               # Public page interactions (pricing toggle, etc.)
│   └── img/
│       └── logo.svg                # ChainWatch Pro SVG logo
│
└── templates/
    ├── base.html                   # Public base layout
    ├── app_base.html               # Authenticated app base layout (nav, sidebar)
    ├── app_home.html               # Post-login landing redirect
    ├── auth/                       # Login, register, verify, reset password
    ├── onboarding/                 # Steps 1–4 of guided onboarding
    ├── app/
    │   ├── dashboard/              # Main + executive dashboards
    │   ├── shipments/              # List, detail, new, edit, import
    │   ├── alerts/                 # Alert index and detail
    │   ├── carrier_intel/          # Carrier intelligence view
    │   ├── optimizer/              # Route optimizer UI
    │   ├── planner/                # Scenario planner UI
    │   ├── risk_map/               # Live risk map
    │   ├── reports/                # Report builder UI
    │   ├── audit/                  # Audit log viewer
    │   └── settings/               # Profile, team, billing, alerts, integrations
    ├── superadmin/                 # Superadmin panel templates
    ├── public/                     # Home, pricing, features, about, blog, demo, contact
    ├── email/                      # Transactional email HTML templates
    ├── errors/                     # 403, 404, 429, 500 error pages
    ├── macros/                     # Reusable Jinja2 macros
    └── partials/                   # Shared nav/footer partials
```

---

## 🚀 Getting Started

### Prerequisites

Make sure you have the following installed before you begin:

| Tool | Version | Install |
|---|---|---|
| Python | 3.11+ | [python.org](https://www.python.org/downloads/) |
| pip | latest | bundled with Python |
| PostgreSQL | 15+ (production) | [postgresql.org](https://www.postgresql.org/download/) |
| Redis | 7+ (optional for dev) | [redis.io](https://redis.io/download/) |
| Docker & Docker Compose | latest (optional) | [docker.com](https://docs.docker.com/get-docker/) |
| Git | any | [git-scm.com](https://git-scm.com/) |

> **Note:** For local development, you can run without Redis and PostgreSQL — ChainWatch Pro auto-detects and falls back to SQLite and in-process scheduling. Redis and Celery are only needed when `USE_REDIS=True` and `CELERY_ENABLED=True`.

---

### Installation

**1. Clone the repository**

```bash
git clone https://github.com/shahram8708/ChainWatch-Pro.git
cd ChainWatch-Pro
```

**2. Create and activate a virtual environment**

```bash
python -m venv venv

# macOS / Linux
source venv/bin/activate

# Windows
venv\Scripts\activate
```

**3. Install Python dependencies**

```bash
pip install -r requirements.txt
```

**4. Copy the environment template and fill in your values**

```bash
cp .env.example .env
```

Open `.env` in your editor and set at minimum:
- `SECRET_KEY` — generate one with: `python -c "import secrets; print(secrets.token_hex(64))"`
- `ENCRYPTION_KEY` — generate one with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- `GEMINI_API_KEY` — from [Google AI Studio](https://aistudio.google.com/)

See the [Environment Variables](#environment-variables) section for the full list.

**5. Run the application**

```bash
python run.py
```

On first startup, ChainWatch Pro automatically creates the SQLite database, all tables, and seeds a default SuperAdmin account (using credentials from your `.env`).

Open your browser at [http://localhost:5000](http://localhost:5000).

---

### Environment Variables

Copy `.env.example` to `.env`. All variables are listed below:

| Variable | Description | Example |
|---|---|---|
| `FLASK_ENV` | Environment name: `development`, `production`, or `testing` | `development` |
| `FLASK_APP` | Flask entrypoint module | `run.py` |
| `SECRET_KEY` | Flask session secret key — **generate a unique value** | `abc123...` |
| `USE_REDIS` | Enable Redis for rate limiting and Celery broker | `False` |
| `CELERY_ENABLED` | Enable Celery async task queue and beat scheduler | `False` |
| `DEV_DATABASE_URL` | SQLite path for development | `sqlite:///instance/chainwatchpro_dev.sqlite3` |
| `DATABASE_URL` | PostgreSQL URI for production | `postgresql://user:pass@localhost:5432/chainwatchpro` |
| `AUTO_CREATE_DB` | Auto-create DB tables on startup (dev only) | `True` |
| `POSTGRES_MAINTENANCE_DB` | Admin DB used to check/create the app DB | `postgres` |
| `REDIS_URL` | Redis connection URL | `redis://localhost:6379/0` |
| `CELERY_BROKER_URL` | Celery broker URL (defaults to `REDIS_URL`) | `redis://localhost:6379/0` |
| `MAIL_SERVER` | SMTP server hostname | `smtp.sendgrid.net` |
| `MAIL_PORT` | SMTP port | `587` |
| `MAIL_USE_TLS` | Enable STARTTLS | `True` |
| `MAIL_USERNAME` | SMTP username | `apikey` |
| `MAIL_PASSWORD` | SMTP password or API key | `SG.xxxxxxxx` |
| `MAIL_DEFAULT_SENDER` | From address for all outbound email | `noreply@chainwatchpro.com` |
| `RAZORPAY_KEY_ID` | Razorpay API key ID | `rzp_live_xxxxxxxxxxxx` |
| `RAZORPAY_KEY_SECRET` | Razorpay API key secret | `your_secret` |
| `RAZORPAY_WEBHOOK_SECRET` | Razorpay webhook signature secret | `whsec_...` |
| `RAZORPAY_PLAN_STARTER_MONTHLY` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `RAZORPAY_PLAN_STARTER_ANNUAL` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `RAZORPAY_PLAN_PROFESSIONAL_MONTHLY` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `RAZORPAY_PLAN_PROFESSIONAL_ANNUAL` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `RAZORPAY_PLAN_ENTERPRISE_MONTHLY` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `RAZORPAY_PLAN_ENTERPRISE_ANNUAL` | Razorpay recurring plan ID (optional) | `plan_xxxxxxx` |
| `GEMINI_API_KEY` | Google AI Gemini API key | `AIza...` |
| `OPENWEATHER_API_KEY` | OpenWeatherMap API key | `a1b2c3...` |
| `TWILIO_ACCOUNT_SID` | Twilio account SID for SMS | `ACxxxxxxxxxxxxxxxx` |
| `TWILIO_AUTH_TOKEN` | Twilio auth token | `your_auth_token` |
| `TWILIO_FROM_NUMBER` | Twilio sender phone number | `+1234567890` |
| `ENCRYPTION_KEY` | Fernet key for encrypting carrier credentials — **generate a unique value** | `base64url_key=` |
| `SUPERADMIN_URL_PREFIX` | URL prefix for the superadmin panel | `/sa-panel` |
| `SUPERADMIN_EMAIL` | Default superadmin login email | `superadmin@chainwatchpro.internal` |
| `SUPERADMIN_PASSWORD` | Default superadmin password — **change in production** | `ChainWatch@SuperAdmin2026!` |
| `SUPERADMIN_SESSION_TIMEOUT` | Superadmin session idle timeout (minutes) | `30` |
| `AI_CACHE_TTL_*` | TTL in seconds for each AI content type (carrier commentary, simulation narrative, executive brief, etc.) | `86400` |
| `PROFILE_PHOTO_UPLOAD_DIR` | Filesystem path for profile photo uploads | `static/uploads/profile_photos` |
| `PROFILE_PHOTO_MAX_BYTES` | Maximum profile photo file size in bytes | `2097152` |
| `SUPPORT_EMAIL` | Support contact address shown in UI | `support@chainwatchpro.com` |

---

### Running the Project

#### Development (SQLite, no Redis required)

```bash
# Make sure FLASK_ENV=development and USE_REDIS=False in .env
python run.py
```

The app runs at `http://localhost:5000` with debug mode enabled.

#### Development with Redis + Celery

If you want background tasks (DRS computation, email, report generation) to run asynchronously:

```bash
# Terminal 1 — Flask app
FLASK_ENV=development USE_REDIS=True CELERY_ENABLED=True python run.py

# Terminal 2 — Celery worker
celery -A celery_worker.celery worker --loglevel=info -Q high,default,low

# Terminal 3 — Celery beat (periodic tasks)
celery -A celery_worker.celery beat --loglevel=info
```

#### Flask CLI commands

```bash
# Run database migrations
flask db upgrade

# Create a new migration after model changes
flask db migrate -m "describe your change"

# Seed the default superadmin manually
flask ensure-superadmin
```

#### Production (Gunicorn)

```bash
FLASK_ENV=production gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 120 run:app
```

---

## 💡 Usage

### First Login

After starting the app, visit `http://localhost:5000` and click **Get Started** or navigate to `/auth/register` to create your first organisation account. Alternatively, log into the SuperAdmin panel at `http://localhost:5000/sa-panel` using the credentials set in your `.env`.

### Onboarding Flow

New organisations are guided through 4 steps:

1. **Company Profile** — industry, company size, monthly shipment volume
2. **Carrier Setup** — add your first carrier(s) from the global carrier library
3. **First Shipment** — create a shipment with origin/destination ports, transport mode, and dates
4. **Notification Preferences** — configure email, SMS, and webhook alert rules

### Creating a Shipment

Navigate to **Shipments → New Shipment** and fill in:
- Transport mode (Ocean FCL/LCL, Air, Road, Rail, Multimodal)
- Origin and destination port codes (e.g. `INBOM` → `NLRTM`)
- Carrier
- Estimated departure and arrival dates
- Optional: external reference, cargo value, container details

Or **import in bulk** from a CSV file using the provided template (`Shipments → Import → Download Template`).

### Reading the Disruption Risk Score

Every shipment card and detail page shows a DRS badge:

| Score Range | Level | Meaning |
|---|---|---|
| 0 – 30 | 🟢 Green | On track, no significant risk factors |
| 31 – 60 | 🟡 Watch | Monitor closely; minor risk factors detected |
| 61 – 80 | 🟠 Warning | High probability of delay; consider alternatives |
| 81 – 100 | 🔴 Critical | Severe disruption likely; immediate action recommended |

The score is computed from carrier on-time history, weather at ports, port congestion data, active news events on the route, and transit progress against schedule.

### Running a Scenario Simulation

Go to **Planner** and enter a hypothetical shipment (ports, mode, cargo value, departure date). ChainWatch Pro will:
1. Compute a projected DRS for that route at that time
2. Query weather, port congestion, and news data
3. Return a Gemini-generated risk narrative with booking recommendations

### Generating a Report

Go to **Reports**, choose a report type (Carrier Performance, Shipment Summary, Disruption Analysis), select a date range, and click **Generate**. Reports run as background tasks and appear in the download queue when ready. PDF and Excel formats are available.

---

## 📡 API Documentation

All endpoints are under `/api/v1` and require an active authenticated session (cookie-based). JSON responses only.

### Core Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/v1/shipment-map-data` | Returns all active shipments with coordinates and DRS for the risk map |
| `GET` | `/api/v1/shipments/<id>/drs-history` | Returns DRS score history for a specific shipment |
| `GET` | `/api/v1/shipments/<id>/optimizer-recommendations` | Returns pending route recommendations for a shipment |
| `GET` | `/api/v1/alerts/unread-count` | Returns count of unread alerts for the current organisation |
| `GET` | `/api/v1/search?q=<query>` | Global search across shipments, carriers, alerts, and recommendations |
| `GET` | `/api/v1/dashboard/metrics` | Returns KPI metrics for dashboard polling |
| `GET` | `/api/v1/carriers/performance` | Returns carrier performance data for the current organisation |
| `GET` | `/api/v1/planner/simulation-status?job_id=<id>` | Polls async simulation task status |
| `POST` | `/api/v1/carriers/<id>/regenerate-commentary` | Triggers AI regeneration of carrier commentary |
| `GET` | `/api/v1/reports/status?job_id=<id>` | Polls async report generation task status |
| `GET` | `/api/v1/admin/ai-cache-stats` | Returns AI cache hit/miss stats (admin+ only) |

### Webhook Endpoint

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/webhooks/razorpay` | Razorpay payment webhook (HMAC-SHA256 verified) |

### Response Format

All API responses follow this structure:

```json
{
  "success": true,
  "data": { ... },
  "error": null
}
```

Error responses:

```json
{
  "success": false,
  "data": null,
  "error": "Human-readable error message"
}
```

---

## ⚙️ Configuration

### config.py

Three configuration classes extend `BaseConfig`:

- **`DevelopmentConfig`** — SQLite, no Redis, debug=True, relaxed cookie security
- **`ProductionConfig`** — PostgreSQL, Redis required, debug=False, `SESSION_COOKIE_SECURE=True`
- **`TestingConfig`** — in-memory rate limiting, CSRF disabled, SQLite

Select the environment by setting `FLASK_ENV` to `development`, `production`, or `testing`.

### AI Cache TTLs

Each AI content type has its own configurable TTL:

| Content Type | Env Variable | Default |
|---|---|---|
| Carrier commentary | `AI_CACHE_TTL_CARRIER_COMMENTARY` | `86400` (24h) |
| Shipment disruption summary | `AI_CACHE_TTL_SHIPMENT_DISRUPTION_SUMMARY` | `900` (15 min) |
| Simulation narrative | `AI_CACHE_TTL_SIMULATION_NARRATIVE` | `3600` (1h) |
| Executive brief | `AI_CACHE_TTL_EXECUTIVE_BRIEF` | `43200` (12h) |
| Alert description | `AI_CACHE_TTL_ALERT_DESCRIPTION` | `0` (no cache) |
| Route event risk | `AI_CACHE_TTL_ROUTE_EVENT_RISK` | `1800` (30 min) |
| Port congestion analysis | `AI_CACHE_TTL_PORT_CONGESTION_ANALYSIS` | `3600` (1h) |

### Subscription Plans and Limits

| Resource | Starter | Professional | Enterprise |
|---|---|---|---|
| Active shipments | 50 | 500 | Unlimited |
| Carriers | 3 | 15 | Unlimited |
| Team members | 2 | 10 | Unlimited |
| Scenario planner runs | 5/month | Unlimited | Unlimited |
| Pricing (monthly) | ₹12,499 | ₹33,299 | Custom |
| Pricing (annual) | ₹1,19,990 | ₹3,19,670 | Custom |

### Celery Beat Schedule (when Celery is enabled)

| Task | Schedule | Purpose |
|---|---|---|
| `poll_carrier_updates` | Every 15 minutes | Refresh carrier tracking data |
| `compute_disruption_scores_all` | Every 15 minutes (offset) | Recompute DRS for all active shipments |
| `ingest_external_data` | Every hour | Pull weather and port congestion data |
| `update_carrier_performance` | Daily at 2:00 AM | Recalculate carrier OTD metrics |

### Feature Flags

Platform operators can toggle features per-organisation from the SuperAdmin panel without deploying code. Feature flags are stored in the `feature_flags` table and evaluated at request time.

---

## 🧪 Testing

ChainWatch Pro does not currently include an automated test suite. The `TestingConfig` in `config.py` provides a test-ready configuration (CSRF disabled, in-memory rate limiting, isolated SQLite database) for when you want to add tests.

To run with the test configuration:

```bash
FLASK_ENV=testing python run.py
```

To contribute tests, the recommended approach is:
- Use `pytest` as the test runner
- Create a `tests/` directory at the project root
- Use Flask's built-in test client (`app.test_client()`) for route-level tests
- Use the `TestingConfig` for all test fixtures

---

## 🐳 Deployment

### Docker Compose (recommended)

The included `docker-compose.yml` spins up all four services with one command.

**1. Configure your environment**

```bash
cp .env.example .env
# Edit .env — set SECRET_KEY, ENCRYPTION_KEY, GEMINI_API_KEY, and all other required values
# Set FLASK_ENV=production, USE_REDIS=True, CELERY_ENABLED=True
```

**2. Build and start**

```bash
docker-compose up --build -d
```

This starts:
- `web` — Gunicorn serving the Flask app on port 5000
- `db` — PostgreSQL 15 with persistent volume
- `redis` — Redis 7 with AOF persistence
- `celery_worker` — Celery worker consuming `high`, `default`, and `low` queues

**3. Run database migrations (first deploy)**

```bash
docker-compose exec web flask db upgrade
```

**4. Verify health**

```bash
docker-compose ps
docker-compose logs -f web
```

### Manual Production Deployment

**1. Install system dependencies**

```bash
# Debian/Ubuntu
sudo apt-get install -y python3.11 python3.11-venv libpq-dev postgresql-client redis-tools
# WeasyPrint requires additional system libs
sudo apt-get install -y libcairo2 libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info
```

**2. Set up the app**

```bash
git clone https://github.com/shahram8708/ChainWatch-Pro.git /srv/chainwatch
cd /srv/chainwatch
python3.11 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # Edit .env with production values
```

**3. Run database migrations**

```bash
FLASK_ENV=production flask db upgrade
```

**4. Start services**

Use a process manager like `systemd` or `supervisor` to manage:

```bash
# Gunicorn
gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 120 run:app

# Celery worker
celery -A celery_worker.celery worker --loglevel=info -Q high,default,low

# Celery beat
celery -A celery_worker.celery beat --loglevel=info
```

**5. Reverse proxy**

Put Nginx or Caddy in front of Gunicorn. Example Nginx location block:

```nginx
location / {
    proxy_pass http://127.0.0.1:5000;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
}
```

### Production Checklist

- `SECRET_KEY` set to a randomly generated 64-byte hex string
- `ENCRYPTION_KEY` set to a freshly generated Fernet key
- `SUPERADMIN_PASSWORD` changed from the default
- `SUPERADMIN_URL_PREFIX` changed to a non-obvious path
- `SESSION_COOKIE_SECURE=True` (set automatically by `ProductionConfig`)
- PostgreSQL running with a dedicated user and restricted privileges
- Redis bound to localhost only (not exposed publicly)
- HTTPS enabled on the reverse proxy
- Email (SMTP) configured and tested
- Razorpay webhook URL registered in Razorpay dashboard pointing to `/webhooks/razorpay`

---

## 🤝 Contributing

Contributions are welcome. Here's how to get involved:

**1. Fork the repository** and clone your fork locally.

**2. Create a feature branch**

```bash
git checkout -b feature/your-feature-name
```

**3. Make your changes**, keeping these conventions in mind:
- Follow existing patterns: blueprint-per-feature, service layer for business logic, models stay thin
- Keep route handlers short — move logic into `app/services/`
- Use the `AuditLog` model for any action that changes data
- Respect the RBAC decorators (`@login_required`, `@role_required(...)`) on all authenticated routes
- Keep Jinja2 templates logic-free — move conditionals to context helpers

**4. Commit with a clear message**

```bash
git commit -m "feat: add carrier webhook status polling"
```

**5. Open a Pull Request** against `main` with a description of what changed and why.

### Reporting a Bug

Open a GitHub Issue with:
- Python version and OS
- Steps to reproduce
- Expected vs actual behaviour
- Relevant log output (from `docker-compose logs` or Flask console)

### Requesting a Feature

Open a GitHub Issue with:
- The problem you're trying to solve
- Your proposed solution
- Any alternative approaches you considered

---

## 🗺 Roadmap

- Disruption Risk Score engine with multi-factor computation
- Gemini AI integration for summaries, commentary, and briefs
- Route optimizer and scenario planner
- Multi-channel notifications (email, SMS, webhooks)
- Razorpay billing with plan enforcement
- SuperAdmin panel with feature flags and org management
- PDF and Excel report generation
- Bulk CSV shipment import
- **Real carrier API integrations** — the `carrier_tracker.py` service includes position interpolation logic, but live carrier API connections (e.g. MSC, Maersk, FedEx) are not yet wired in
- **Mobile push notifications** — Twilio SMS is live; a native mobile app or PWA push notification layer would complement it
- **Automated test suite** — `TestingConfig` exists and is ready; the test directory is not yet populated
- **Webhooks inbound (carrier events)** — the outbound webhook service is complete; inbound carrier event webhooks could replace polling
- **Multi-currency support** — billing is INR-native; the `default_currency` column on `Organisation` suggests multi-currency was planned
- **Public API with token auth** — current API requires session auth; a token-based API (JWT or API key) would enable external integrations
- **Slack app integration** — Slack webhook delivery is implemented; a full Slack app with slash commands could follow

---

---

## 🙏 Acknowledgements

- **[Google Generative AI / Gemini](https://ai.google.dev/)** — the AI backbone powering disruption summaries, route risk assessments, carrier commentary, and executive briefs
- **[Flask](https://flask.palletsprojects.com/)** and the entire Pallets ecosystem for a framework that stays out of your way
- **[Razorpay](https://razorpay.com/)** for making INR billing genuinely straightforward
- **[WeasyPrint](https://weasyprint.org/)** for server-side PDF generation without a headless browser
- **[OpenWeatherMap](https://openweathermap.org/api)** for port weather data
- **[Twilio](https://www.twilio.com/)** for SMS alert delivery
- **[Bootstrap 5](https://getbootstrap.com/)** and Bootstrap Icons for the UI foundation
- **[Celery](https://docs.celeryq.dev/)** for making background task orchestration reliable and observable