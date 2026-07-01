# Mortgage CRM — Django Backend (Phase 1)

Server-rendered Django app with role-based login, scoped permissions, and working CRUD.

## Run locally

```bash
cd server
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python manage.py migrate
./venv/bin/python manage.py seed          # demo users + sample data
./venv/bin/python manage.py runserver
```

Open http://127.0.0.1:8000 and log in.

## Demo logins (password: `password123`)

| Username | Role | Sees |
|---|---|---|
| `ceo` | CEO / Managing Director | Everything (also Django admin) |
| `director` | Sales Director | Leads (assign), advisors, sales reports, limited users |
| `ops` | Mortgage Operations Manager | Leads, full tasks/banks/documents |
| `advisor` | Mortgage Advisor | Own leads only + personal target dashboard |
| `accountant` | Accountant / Finance Officer | Finance, view-only leads |

## What works (Phase 1)

- Custom user with 5 roles + per-advisor monthly targets
- Login / logout, role-based dashboard (advisor gets target dashboard, others get management dashboard)
- **Scoped permission matrix** enforced in views + sidebar visibility (`crm/permissions.py`)
- "Advisor sees only own leads/tasks/documents" data scoping
- Leads: list / filter / create / detail / edit
- Users: list / create (assign role + targets) / edit
- Referral Partners: list + create with mandatory fields & document upload
- Banks, Advisors, Tasks, Documents list views
- Roles & Permissions matrix page
- Django admin at `/admin/` (login: ceo)

## Next phases
- Lead detail workflow (stages, title-deed, application tracking)
- Finance & Reports modules
- Auto-calculated advisor targets from real activity
- Document verify/reject workflow
