# HabitSphere – Habit Tracking, Analytics, Reminders & Account Recovery

HabitSphere is a full-stack habit tracking and analytics application designed to help users create routines, record progress, understand consistency, and stay accountable.

The current version is a responsive single-page web application (SPA) powered by a framework-free Python JSON API, MySQL, and an SMTP-based email system. In addition to habit tracking and analytics, the application now includes automated reminder emails for unfinished habits and a secure forgot-password flow using email OTP verification.

## Key features

### Authentication and account management

- User registration with full name, email, and password validation
- Secure login and logout
- Server-side browser sessions
- Passwords are stored as salted PBKDF2-SHA256 hashes rather than plain text
- **Forgot Password** feature available from the login screen
- Password recovery through:
  1. Entering the registered email address
  2. Receiving a verification OTP by email
  3. Verifying the OTP
  4. Creating a new password
- OTPs and reset authorization are stored securely using hashes
- OTP expiry and reset-session expiry are enforced
- Maximum OTP-attempt protection
- Resend-OTP cooldown
- Reset tokens are single-use
- Active sessions can be revoked after a successful password reset

### Habit management

- Create habits with:
  - Habit name
  - Description
  - Category
  - Goal type
  - Target count
  - Active/inactive status
- View habit details
- Edit habits
- Activate/deactivate habits
- Delete habits and their completion history
- Search habits
- Filter by category, goal type, and status
- Habit start date is recorded automatically

### Daily habit tracking

- Track habits by date
- Record completion counts
- Mark a target as completed
- Add optional notes
- Use a seven-day calendar selector around the selected date
- Target-aware completion states
- Dashboard shows today's active habits and their completion status

### Dashboard

The dashboard provides a quick view of current progress, including:

- Total habits
- Active habits
- Today's completed habits
- Current streak
- Longest streak
- Overall completion percentage
- Today's completion percentage
- Seven-day activity chart
- Weekly goal progress
- Monthly goal progress
- Habit-level streak information
- Rule-based consistency suggestions
- Quick actions for tracking habits and viewing reports

### Analytics and insights

HabitSphere calculates analytics from actual MySQL completion records, including:

- Completion percentage
- Current streak
- Longest streak
- Consistency score
- Success rate
- Missed days
- Most consistent habit
- Habit needing attention
- Overall completion percentage

The Insights section supports:

- 7-day / weekly analysis
- 30-day / monthly analysis

The dashboard suggestion system is rule-based and does not require an AI service.

### Reports

The Reports section can generate:

- Daily CSV reports
- Weekly CSV reports
- Monthly CSV reports
- Daily TXT reports
- Weekly TXT reports
- Monthly TXT reports

Reports are generated from the application's current MySQL data.

### Charts and visualisation

HabitSphere uses Matplotlib to generate charts from current analytics and completion data, including:

- Habit completion bar chart
- Completed-versus-missed pie chart
- Seven-day progress chart
- Six-month/monthly trend chart

Generated chart files are stored in the application's chart output directory.

## Email reminder system

HabitSphere includes an SMTP email reminder service that helps users stay consistent.

### Daily reminder behaviour

The reminder service checks the user's active **daily** habits and identifies habits that:

- Are active
- Have already started
- Are scheduled as daily habits
- Have not reached their target for the current date
- Have not already received a daily reminder for that date

When pending daily habits are found, HabitSphere sends an email containing:

- User's name
- Number of pending daily habits
- Habit names
- Categories
- Goal types
- Target counts
- Current progress
- Current date
- A reminder to complete the habits
- An **Open HabitSphere** button/link that takes the user back to the configured website

The purpose of the email is to tell the user that their habits are not yet complete and encourage them to open HabitSphere and finish their daily targets.

The email is provided in both:

- `text/plain`
- `text/html`

The HTML email follows the HabitSphere visual design and safely escapes habit/user content before inserting it into HTML.

### Reminder history

A successful reminder is recorded in `HABIT_REMINDERS` only after the email has been successfully sent.

This prevents failed email attempts from being treated as successfully delivered reminders and helps prevent duplicate reminders for the same habit/date.

### Reminder scheduler

`reminder_scheduler.py` runs the reminder service in a background daemon thread.

The scheduler:

- Can be enabled or disabled through environment configuration
- Uses a configurable reminder time
- Uses a configurable check interval
- Does not block the HTTP server
- Avoids starting duplicate scheduler threads
- Calls the existing reminder service rather than duplicating reminder business logic
- Logs scheduler activity and failures

Relevant environment settings include:

```text
REMINDER_ENABLED=true
REMINDER_HOUR=20
REMINDER_MINUTE=0
REMINDER_CHECK_INTERVAL_SECONDS=300
```

The actual values can be changed according to the local setup.

## Forgot-password / password-reset flow

The current version includes a complete email-based password recovery workflow.

### Flow

```text
Login page
    │
    ├── Forgot Password
    │
    ▼
Enter registered email
    │
    ▼
HabitSphere generates a 6-digit OTP
    │
    ▼
OTP is sent through SMTP email
    │
    ▼
User enters OTP
    │
    ├── Invalid → attempt counter increases
    ├── Too many attempts → verification rejected
    └── Expired → user must start again
    │
    ▼
OTP verified
    │
    ▼
Short-lived reset token created
    │
    ▼
User enters new password
    │
    ▼
Password hash updated in MySQL
    │
    ▼
Reset token marked as used
    │
    ▼
Password reset successful
```

Security-related limits currently include:

- OTP expiry: 10 minutes
- Reset-token expiry: 10 minutes
- Maximum OTP attempts: 5
- Resend cooldown: 60 seconds
- Reset tokens are stored as hashes
- Reset tokens are single-use
- Passwords are re-hashed using the application's password manager

The forgot-password request also uses a generic response so the application does not unnecessarily reveal whether an email address is registered.

## Technology stack

| Layer | Technologies |
| --- | --- |
| Frontend | HTML, CSS, JavaScript |
| Backend | Python standard-library HTTP server + custom JSON API |
| Database | MySQL + `mysql-connector-python` |
| Analytics | Pandas + Python date/time handling |
| Visualisation | Matplotlib |
| Authentication | Python PBKDF2-SHA256 password hashing + server-side sessions |
| Email | Python SMTP / MIME email |
| Password recovery | Email OTP + hashed reset token |
| Settings | JSON/environment configuration |

No Flask, Django, React, Bootstrap, Tailwind CSS, or other web framework is required.

## Project architecture

```text
Browser SPA
(index.html + CSS + JavaScript)
          │
          │ fetch() JSON requests
          ▼
Python HTTP Server
(app.py)
          │
          ├──────── Authentication / API routing
          │
          ▼
HabitSphere Services
(habit_tracker.py)
          │
          ├── User authentication
          ├── Password reset
          ├── Habit management
          ├── Habit tracking
          ├── Dashboard statistics
          ├── Analytics
          ├── Reports
          └── Charts
          │
          ├───────────────────────┐
          ▼                       ▼
MySQL Database              Email Services
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
             ReminderService     Password-reset
                                  OTP email
                    │
                    ▼
            ReminderScheduler
```

The frontend remains a single page. JavaScript switches between dashboard, habits, tracker, insights, reports, and settings views without requiring separate page navigation.

## MySQL database

The database name is:

```text
habit_tracker
```

The current MySQL database contains these seven tables:

| Table | Purpose |
| --- | --- |
| `users` | Registered users and securely hashed passwords |
| `habits` | Habit definitions, categories, targets, schedules, start dates, and statuses |
| `habit_completion` | Date-based completion counts, completion state, and notes |
| `habit_analytics` | Habit performance and analytics information |
| `improvement_tips` | Rule-based improvement/suggestion information |
| `password_reset_tokens` | OTP/reset authorization records used by forgot-password |
| `habit_reminders` | History of successfully sent habit reminder emails |

### Current database table list

```text
mysql> SHOW TABLES;
+-------------------------+
| Tables_in_habit_tracker |
+-------------------------+
| habit_analytics         |
| habit_completion        |
| habit_reminders         |
| habits                  |
| improvement_tips        |
| password_reset_tokens   |
| users                   |
+-------------------------+
```

### Database relationships

```text
users
  │
  └──< habits
          │
          ├──< habit_completion
          ├──< habit_analytics
          ├──< improvement_tips
          └──< habit_reminders

users
  │
  └──< password_reset_tokens
```

Foreign keys are used to maintain relationships between users, habits, completion records, analytics, tips, reminders, and password-reset records. Child records can be removed according to the configured cascade rules.

## Project files

The main current application components are:

```text
HabitSphere/
├── app.py                  # HTTP server and JSON API routing
├── habit_tracker.py        # Core OOP services and business logic
├── reminder_service.py     # SMTP email creation and reminder delivery
├── reminder_scheduler.py   # Background reminder scheduler
├── index.html              # SPA markup
├── app.js                  # Frontend interaction and API calls
├── style.css               # Responsive UI styling
├── configuration.py        # Application/configuration support
├── requirements.txt        # Python dependencies
└── README.md               # Project documentation
```

Runtime-generated output can include reports, charts, logs, and other configured application data.

> **Note:** `habitsphere.py` and `habits_data.json` belong to the earlier terminal/JSON prototype. The current web application uses the MySQL-backed services and SPA files listed above.

## How the application works

1. Start MySQL.
2. Start the Python application.
3. Open the HabitSphere website in a browser.
4. Register an account or sign in.
5. Create one or more habits.
6. Track daily completion and notes.
7. The dashboard retrieves live data from MySQL.
8. Analytics are calculated from completion records.
9. Reports and charts can be generated from the current data.
10. If active daily habits remain incomplete, the reminder service can send an email directing the user back to HabitSphere.
11. If the user forgets the password, the login page provides an email OTP-based password recovery flow.

## Installation requirements

- Python 3.13 or a compatible modern Python 3 release
- MySQL Server
- A MySQL user with permission to create/use the `habit_tracker` database
- SMTP email account/configuration for email features

## Virtual environment setup

From the project root in PowerShell:

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
.\venv\Scripts\Activate.ps1
```

## MySQL setup

1. Start MySQL Server.
2. Configure the MySQL connection used by `habit_tracker.py` / the application's configuration.
3. Initialize the database schema using the project's schema initialization process.
4. Verify the database contains the seven current tables listed above.

A typical verification command is:

```sql
USE habit_tracker;
SHOW TABLES;
```

Expected tables:

```text
habit_analytics
habit_completion
habit_reminders
habits
improvement_tips
password_reset_tokens
users
```

## Email configuration

Email functionality requires SMTP configuration.

Typical configuration values include:

```text
SMTP_SERVER=<smtp-server>
SMTP_PORT=<smtp-port>
SENDER_EMAIL=<sender-email>
SENDER_PASSWORD=<smtp-password-or-app-password>
APP_BASE_URL=http://127.0.0.1:8000
```

Do not commit real SMTP passwords or other secrets to source control.

### Why `APP_BASE_URL` matters

Reminder emails contain an **Open HabitSphere** link. The link is generated from the configured application base URL.

For local development:

```text
APP_BASE_URL=http://127.0.0.1:8000
```

For a deployed application, use the actual website URL.

## Running the application

From the project root with the virtual environment activated:

```powershell
python app.py
```

Then open:

```text
http://127.0.0.1:8000
```

Keep the terminal open while using the application.

Press:

```text
Ctrl + C
```

to stop the server.

## Email reminder operation

For reminder emails to work:

1. Configure the SMTP server.
2. Configure the sender email and SMTP credentials.
3. Configure the application base URL.
4. Enable the reminder scheduler.
5. Start `app.py`.
6. Create an active daily habit.
7. Leave the habit incomplete for the reminder check.
8. The scheduler will check pending daily habits after the configured reminder time.
9. If a pending habit is found, HabitSphere sends the reminder email.
10. After successful delivery, the reminder is recorded in `habit_reminders`.

The reminder service avoids sending the same daily reminder repeatedly for the same habit/date.

## Forgot-password operation

To use password recovery:

1. Open the HabitSphere login page.
2. Click **Forgot Password**.
3. Enter the registered email address.
4. Check the email inbox for the six-digit OTP.
5. Enter the OTP in HabitSphere.
6. If verification succeeds, continue to the reset-password step.
7. Enter and confirm the new password.
8. Submit the new password.
9. Sign in using the new password.

If the OTP expires, the user must request a new one. The resend action is protected by a cooldown.

## Security considerations

HabitSphere includes several security measures:

- Passwords are never stored as plain text.
- Passwords use salted PBKDF2-SHA256 hashing.
- Server-side sessions use random tokens and expiration.
- Browser session cookies can be configured with secure attributes.
- SQL operations use parameterized queries.
- Habit/user API operations require authentication where appropriate.
- Password-reset OTPs are hashed before storage.
- Password-reset authorization tokens are hashed before storage.
- OTPs expire after a limited period.
- Reset tokens expire and are single-use.
- OTP attempts are limited.
- Resending OTPs is rate-limited with a cooldown.
- Reminder HTML safely escapes inserted user/habit content.
- Forgot-password requests use a generic response to reduce account-enumeration risk.

### Production recommendations

For production deployment:

- Use HTTPS.
- Store secrets in environment variables or a secrets manager.
- Use secure cookies.
- Do not commit SMTP credentials.
- Do not commit database passwords.
- Use a persistent session store instead of in-memory sessions.
- Use a production-grade WSGI/ASGI server if the architecture is later migrated to a web framework/server stack.
- Configure proper SMTP sender authentication.
- Restrict database permissions to the minimum required.

## Reports and generated files

Reports and charts are generated from live application data.

Typical report outputs include:

```text
reports/
├── csv/
└── txt/
```

Typical chart output:

```text
static/charts/
```

The exact directories may vary depending on the current local configuration.

## Testing

The project includes focused tests for email functionality. The reminder email tests verify:

- Daily reminder subject generation
- Singular/plural habit wording
- Required content in HTML email
- Correct HabitSphere website URL
- HTML escaping of malicious habit names
- Multipart plain-text + HTML email construction
- Invalid-recipient handling
- Graceful failure when SMTP configuration is incomplete

SMTP is mocked in these tests so real emails are not sent during the test suite.

## Known limitations

- Server-side sessions are stored in application memory, so restarting the server logs users out.
- Email delivery depends on correct SMTP configuration and network access.
- Reminder scheduling runs while the application process is running.
- The application is primarily configured for local development.
- Production deployment, HTTPS termination, persistent sessions, and production-grade background job infrastructure are outside the current local setup.

## Future enhancements

Possible future improvements include:

- Per-user application preferences
- Persistent session storage
- More advanced reminder scheduling
- Calendar heat maps
- More detailed historical analytics
- Additional notification channels
- Production deployment configuration
- Background job queue for large-scale reminder delivery
- More advanced rule-based improvement recommendations

## Author

**P. Dinesh Ajay**

HabitSphere was developed as a Python full-stack habit tracking, analytics, reminder, and account-recovery project.
