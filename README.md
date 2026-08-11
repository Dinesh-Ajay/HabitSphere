# HabitSphere – Habit Tracking and Analytics System

HabitSphere is a full-stack habit tracking application for creating routines, recording daily progress, reviewing consistency, and generating data-driven reports. It is built as a responsive single-page application (SPA) with a framework-free Python JSON API and a MySQL database.

## Key features

- Secure user registration, login, logout, and server-side sessions
- Create, view, edit, delete, search, filter, activate, and deactivate habits
- Daily habit tracking with completion counts, notes, target-aware completion, and a date selector
- Dashboard with active habits, progress, streaks, weekly activity, and current calendar week/month goals
- Habit analytics: completion percentage, current streak, longest streak, consistency score, success rate, and missed days
- Weekly and monthly Insight views
- Downloadable CSV and TXT reports generated from MySQL data
- Matplotlib bar, pie, weekly-progress, and monthly-trend charts
- JSON-managed application preferences: theme, default section, weekly/monthly goals, and export preference
- Logging and validation for important application operations

## Technology stack

| Layer | Technologies |
| --- | --- |
| Frontend | HTML, CSS, JavaScript |
| Backend | Python with the built-in `http.server` and a custom JSON API handler |
| Database | MySQL with `mysql-connector-python` |
| Analytics | Pandas, Python date/time handling |
| Visualisation | Matplotlib |
| Settings | JSON |

No Flask, Django, React, Bootstrap, Tailwind CSS, or other web framework is used.

## Project architecture

```text
Browser SPA (index.html + static/css/style.css + static/js/app.js)
        │ fetch() JSON requests
        ▼
Python built-in HTTP server (app.py)
        ▼
Business logic, validation, analytics, reports, charts (habit_tracker.py)
        ▼
MySQL database + JSON settings + generated reports/charts/logs
```

The frontend remains on one HTML page. JavaScript shows and hides SPA sections without full page navigation.

## MySQL database overview

The database name is `habit_tracker`. The schema in `database/schema.sql` contains these tables:

| Table | Purpose |
| --- | --- |
| `USERS` | Registered account details and password hashes |
| `HABITS` | User habits, categories, goals, targets, start dates, and statuses |
| `HABIT_COMPLETION` | Dated completion counts and notes |
| `HABIT_ANALYTICS` | Persisted habit performance metrics |
| `IMPROVEMENT_TIPS` | Reserved by the approved schema for rule-based suggestions |

## ER diagram

```text
USERS (user_id)
  └──< HABITS (habit_id, user_id)
         ├──< HABIT_COMPLETION (completion_id, habit_id)
         ├──< HABIT_ANALYTICS (analytics_id, habit_id)
         └──< IMPROVEMENT_TIPS (tip_id, habit_id)
```

`USERS → HABITS` and `HABITS →` child tables are enforced with foreign keys and cascade deletion rules in the schema.

## Project folder structure

```text
HabitSphere/
├── app.py                       # Framework-free HTTP server and JSON API routes
├── habit_tracker.py             # OOP services: DB, auth, habits, tracking, analytics, reports, charts
├── index.html                   # Single-page application markup
├── requirements.txt             # Pinned Python packages
├── database/
│   └── schema.sql               # MySQL database schema
├── json/
│   └── settings.json            # Application and database configuration
├── static/
│   ├── css/style.css            # Responsive SPA styling
│   ├── js/app.js                # SPA interaction and API requests
│   └── charts/                  # Matplotlib charts generated at runtime
├── logs/
│   └── app.log                  # Application logs
├── reports/
│   ├── csv/                     # Generated CSV reports
│   └── txt/                     # Generated TXT reports
├── venv/                        # Local virtual environment (created locally)
├── habitsphere.py               # Earlier JSON-based terminal prototype
└── habits_data.json             # Data file for the earlier terminal prototype
```

The current web application uses `app.py`, `habit_tracker.py`, MySQL, and `json/settings.json`. The legacy `habitsphere.py` and `habits_data.json` files are not used by the SPA.

## How the application works

1. A user registers and signs in through the SPA.
2. The Python server creates a secure, short-lived server-side session and sends an `HttpOnly` cookie.
3. Habits and completions are stored in MySQL through JSON API requests.
4. The dashboard retrieves live MySQL totals, today's active habits, calendar-week/month goals, and seven-day activity.
5. Analytics calculate performance values from real completion records.
6. Reports and charts are generated from those analytics and completion records.

## Analytics features

- Habit completion percentage
- Current and longest daily streak
- Consistency score
- Success rate
- Missed days
- Most and least consistent habits
- Week (7-day) and month (30-day) Insight periods

## Reports and visualisation

The Reports page generates:

- Daily, weekly, and monthly CSV reports
- Daily, weekly, and monthly TXT reports
- Habit completion bar chart
- Completed-versus-missed pie chart
- Seven-day progress line chart
- Six-month trend line chart

Generated files are stored in `reports/` and `static/charts/`.

## Installation requirements

- Python 3.13 or compatible Python 3 release
- MySQL Server running locally
- A MySQL account permitted to create/use the `habit_tracker` database

## Virtual environment setup

From the project root in PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation for the current terminal session:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\venv\Scripts\Activate.ps1
```

## MySQL database setup

1. Start your local MySQL Server.
2. Update the `database` object in `json/settings.json` with your MySQL host, port, username, password, and database name.
3. Initialise the schema once:

```powershell
python -c "from habit_tracker import DatabaseManager; DatabaseManager().initialize_schema()"
```

The supplied schema creates the `habit_tracker` database and the required tables when they do not already exist.

## Configure settings

Two settings areas are available:

- `json/settings.json` contains database/server configuration and default application preferences.
- The in-app **Settings** SPA page updates theme, default section, weekly goal, monthly goal, and default report export format immediately.

For local development, the default server address is configured as `127.0.0.1:8000`.

## Run the application

Activate the virtual environment, then run:

```powershell
python app.py
```

Open the application in a browser:

```text
http://127.0.0.1:8000
```

Keep the terminal open while using the application. Press `Ctrl + C` to stop the server.

## Security considerations

- Passwords are stored with PBKDF2-SHA256 hashing and a unique random salt.
- Passwords are not stored as plain text.
- Sessions use random server-side tokens with an expiry period.
- Session cookies are sent with `HttpOnly` and `SameSite=Lax` attributes.
- API operations require an authenticated session where applicable.
- SQL parameters are passed through `mysql-connector-python` parameterised queries.

For production deployment, move database credentials out of `settings.json`, use HTTPS, set `Secure` cookies, and use persistent session storage.

## Known limitations

- Sessions are held in server memory; restarting the server logs users out.
- JSON preferences are application-wide rather than per-user.
- The approved database schema stores completion dates but does not include a separate completion-time column.
- The project is configured for local development and does not include production deployment configuration.

## Future enhancements

- Per-user preference storage
- Persistent session storage
- Scheduled reminders
- Rule-based improvement-tip generation using `IMPROVEMENT_TIPS`
- Calendar month view and historical completion heat map
- Account password reset workflow
- Production configuration with environment-based secrets and HTTPS

## Author

**P. Dinesh Ajay**

HabitSphere was developed as a Python full-stack habit tracking and analytics project.
