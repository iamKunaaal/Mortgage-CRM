# Build Log

## 2026-08-30 — Outlook / Microsoft 365 integration (all roles)

Client wants everything from inside the CRM — user never opens Outlook. Built full
Microsoft Graph integration (per-user delegated OAuth), stdlib urllib only (no new deps).

**Migration 0052:** OutlookAccount (per-user tokens).

- Config: AppSetting 'outlook' {client_id, client_secret, tenant_id, redirect_uri}; CEO/Super-Admin
  saves it from the Mailbox page (shown when not configured).
- OAuth: `/outlook/connect/` → MS login → `/outlook/callback/` stores tokens; auto-refresh via
  refresh_token; `/outlook/disconnect/`. Scopes: Mail.Read, Mail.Send, Calendars.ReadWrite, User.Read, offline_access.
- Mailbox `/mailbox/` (all roles, sidebar): inbox (25 latest) + compose/send from own Outlook.
- Calendar `/outlook/calendar/` (all roles, sidebar): next-30-day events + create meeting.
- Graceful states: not-configured (admin form) / not-connected (Connect button) / errors → reconnect.
  Integration is never a hard dependency — CRM works fully without it.

**Needs from client (to go live):** Azure AD app in their M365 tenant — Client ID, Client Secret,
Tenant ID; redirect URI `https://<host>/outlook/callback/`; delegated API permissions above (+ admin consent).

**Status:** migration applied, `manage.py check` clean, all pages 200, config-save + connect-redirect
flows tested. Live mail send/read untestable without real Azure creds. NOT pushed.
