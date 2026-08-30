"""
Outlook / Microsoft 365 integration — everything happens inside the CRM.
Each user connects their own Outlook once (delegated OAuth); the CRM then
reads/sends mail and reads/creates calendar events on their behalf via
Microsoft Graph. No third-party SDK — plain stdlib urllib.

Requires an Azure AD app (client_id / client_secret / tenant_id) saved in
Settings → Outlook. Until that is set, pages show a friendly "not configured"
state and nothing breaks (integration is never a hard dependency).
"""
import json
import urllib.parse
import urllib.request
import urllib.error
from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import AppSetting, OutlookAccount
from .views import _audit_event

GRAPH = 'https://graph.microsoft.com/v1.0'
SCOPES = 'offline_access User.Read Mail.Read Mail.Send Calendars.ReadWrite'


# ---- config -----------------------------------------------------------------
def outlook_config():
    row = AppSetting.objects.filter(key='outlook').first()
    return row.value if row else {}


def _configured(cfg=None):
    cfg = cfg or outlook_config()
    return bool(cfg.get('client_id') and cfg.get('client_secret'))


def _tenant(cfg):
    return cfg.get('tenant_id') or 'organizations'


def _authority(cfg):
    return f'https://login.microsoftonline.com/{_tenant(cfg)}/oauth2/v2.0'


def _redirect_uri(request):
    return request.build_absolute_uri(reverse('outlook_callback'))


# ---- low-level HTTP ---------------------------------------------------------
def _post_form(url, data):
    body = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=body, method='POST',
                                 headers={'Content-Type': 'application/x-www-form-urlencoded'})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode())


def _graph(method, path, token, payload=None):
    url = path if path.startswith('http') else GRAPH + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Authorization': f'Bearer {token}',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read().decode()
        return json.loads(raw) if raw else {}


# ---- token management -------------------------------------------------------
def _valid_token(acct):
    """Return a live access token for this account, refreshing if needed. None on failure."""
    cfg = outlook_config()
    if not _configured(cfg) or not acct or not acct.refresh_token:
        return None
    if acct.access_token and acct.expires_at and acct.expires_at > timezone.now() + timedelta(seconds=120):
        return acct.access_token
    try:
        tok = _post_form(_authority(cfg) + '/token', {
            'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
            'grant_type': 'refresh_token', 'refresh_token': acct.refresh_token,
            'scope': SCOPES, 'redirect_uri': cfg.get('redirect_uri', '')})
    except urllib.error.URLError:
        return None
    acct.access_token = tok.get('access_token', '')
    if tok.get('refresh_token'):
        acct.refresh_token = tok['refresh_token']
    acct.expires_at = timezone.now() + timedelta(seconds=int(tok.get('expires_in', 3600)))
    acct.save(update_fields=['access_token', 'refresh_token', 'expires_at'])
    return acct.access_token or None


def _account(user):
    return OutlookAccount.objects.filter(user=user).first()


# ---- OAuth connect flow -----------------------------------------------------
@login_required
def outlook_connect(request):
    cfg = outlook_config()
    if not _configured(cfg):
        messages.error(request, 'Outlook is not configured yet. Ask an admin to add the Azure app in Settings.')
        return redirect('mailbox')
    params = {
        'client_id': cfg['client_id'], 'response_type': 'code',
        'redirect_uri': _redirect_uri(request), 'response_mode': 'query',
        'scope': SCOPES, 'state': str(request.user.pk),
        'prompt': 'select_account'}
    return redirect(_authority(cfg) + '/authorize?' + urllib.parse.urlencode(params))


@login_required
def outlook_callback(request):
    cfg = outlook_config()
    code = request.GET.get('code')
    if request.GET.get('error') or not code:
        messages.error(request, 'Outlook connection cancelled or failed.')
        return redirect('mailbox')
    try:
        tok = _post_form(_authority(cfg) + '/token', {
            'client_id': cfg['client_id'], 'client_secret': cfg['client_secret'],
            'grant_type': 'authorization_code', 'code': code,
            'redirect_uri': _redirect_uri(request), 'scope': SCOPES})
    except urllib.error.URLError as e:
        messages.error(request, f'Could not complete Outlook sign-in: {e}')
        return redirect('mailbox')
    acct, _ = OutlookAccount.objects.get_or_create(user=request.user)
    acct.access_token = tok.get('access_token', '')
    acct.refresh_token = tok.get('refresh_token', acct.refresh_token)
    acct.expires_at = timezone.now() + timedelta(seconds=int(tok.get('expires_in', 3600)))
    acct.save()
    # fetch the mailbox address
    try:
        me = _graph('GET', '/me?$select=mail,userPrincipalName', acct.access_token)
        acct.ms_email = me.get('mail') or me.get('userPrincipalName', '')
        acct.save(update_fields=['ms_email'])
    except urllib.error.URLError:
        pass
    _audit_event(request, 'Outlook connected', acct.ms_email)
    messages.success(request, f'Outlook connected: {acct.ms_email or "your account"}.')
    return redirect('mailbox')


@login_required
@require_POST
def outlook_disconnect(request):
    OutlookAccount.objects.filter(user=request.user).delete()
    _audit_event(request, 'Outlook disconnected', '')
    messages.success(request, 'Outlook disconnected.')
    return redirect('mailbox')


# ---- admin config save ------------------------------------------------------
@login_required
@require_POST
def outlook_settings_save(request):
    if request.user.role not in ('CEO', 'SUPER_ADMIN'):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied
    AppSetting.objects.update_or_create(key='outlook', defaults={'value': {
        'client_id': request.POST.get('client_id', '').strip(),
        'client_secret': request.POST.get('client_secret', '').strip(),
        'tenant_id': request.POST.get('tenant_id', '').strip(),
        'redirect_uri': _redirect_uri(request)}})
    _audit_event(request, 'Outlook settings saved', '')
    messages.success(request, 'Outlook settings saved.')
    return redirect(request.POST.get('next') or 'mailbox')


# ---- mailbox ----------------------------------------------------------------
@login_required
def mailbox(request):
    cfg = outlook_config()
    acct = _account(request.user)
    ctx = {'active_nav': 'Mailbox', 'configured': _configured(cfg),
           'connected': bool(acct and acct.refresh_token), 'acct': acct,
           'is_admin': request.user.role in ('CEO', 'SUPER_ADMIN'), 'cfg': cfg,
           'messages_list': [], 'error': ''}
    if ctx['connected']:
        token = _valid_token(acct)
        if not token:
            ctx['error'] = 'Session expired — please reconnect Outlook.'
        else:
            try:
                data = _graph('GET', '/me/mailFolders/inbox/messages?$top=25&$select='
                              'subject,from,receivedDateTime,bodyPreview,isRead,webLink', token)
                ctx['messages_list'] = data.get('value', [])
                acct.last_sync = timezone.now(); acct.save(update_fields=['last_sync'])
            except urllib.error.HTTPError as e:
                ctx['error'] = f'Outlook error ({e.code}). Try reconnecting.'
            except urllib.error.URLError:
                ctx['error'] = 'Could not reach Outlook right now.'
    return render(request, 'crm/mailbox.html', ctx)


@login_required
@require_POST
def mail_send(request):
    acct = _account(request.user)
    token = _valid_token(acct)
    if not token:
        messages.error(request, 'Connect Outlook first.')
        return redirect('mailbox')
    to = [a.strip() for a in request.POST.get('to', '').replace(';', ',').split(',') if a.strip()]
    if not to:
        messages.error(request, 'Add at least one recipient.')
        return redirect(request.POST.get('next') or 'mailbox')
    payload = {'message': {
        'subject': request.POST.get('subject', '').strip(),
        'body': {'contentType': 'HTML', 'content': request.POST.get('body', '').replace('\n', '<br>')},
        'toRecipients': [{'emailAddress': {'address': a}} for a in to]},
        'saveToSentItems': True}
    try:
        _graph('POST', '/me/sendMail', token, payload)
        _audit_event(request, 'Email sent', ', '.join(to)[:250])
        messages.success(request, f'Email sent to {", ".join(to)}.')
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        messages.error(request, f'Send failed: {e}')
    return redirect(request.POST.get('next') or 'mailbox')


# ---- calendar ---------------------------------------------------------------
@login_required
def outlook_calendar(request):
    acct = _account(request.user)
    ctx = {'active_nav': 'OutlookCal', 'connected': bool(acct and acct.refresh_token),
           'configured': _configured(), 'events': [], 'error': ''}
    if ctx['connected']:
        token = _valid_token(acct)
        if not token:
            ctx['error'] = 'Session expired — please reconnect Outlook.'
        else:
            start = timezone.now().isoformat()
            end = (timezone.now() + timedelta(days=30)).isoformat()
            try:
                data = _graph('GET', f'/me/calendarView?startDateTime={start}&endDateTime={end}'
                              '&$orderby=start/dateTime&$top=50&$select=subject,start,end,location,organizer',
                              token)
                ctx['events'] = data.get('value', [])
            except (urllib.error.HTTPError, urllib.error.URLError):
                ctx['error'] = 'Could not load calendar right now.'
    return render(request, 'crm/outlook_calendar.html', ctx)


@login_required
@require_POST
def outlook_event_create(request):
    acct = _account(request.user)
    token = _valid_token(acct)
    if not token:
        messages.error(request, 'Connect Outlook first.')
        return redirect('outlook_calendar')
    tz = 'Asia/Dubai'
    start = request.POST.get('start')  # 'YYYY-MM-DDTHH:MM'
    end = request.POST.get('end')
    if not start or not end:
        messages.error(request, 'Start and end are required.')
        return redirect('outlook_calendar')
    attendees = [a.strip() for a in request.POST.get('attendees', '').replace(';', ',').split(',') if a.strip()]
    payload = {'subject': request.POST.get('subject', '').strip(),
               'body': {'contentType': 'HTML', 'content': request.POST.get('body', '')},
               'start': {'dateTime': start, 'timeZone': tz},
               'end': {'dateTime': end, 'timeZone': tz},
               'location': {'displayName': request.POST.get('location', '').strip()},
               'attendees': [{'emailAddress': {'address': a}, 'type': 'required'} for a in attendees]}
    try:
        _graph('POST', '/me/events', token, payload)
        _audit_event(request, 'Calendar event created', request.POST.get('subject', '')[:250])
        messages.success(request, 'Event created in your Outlook calendar.')
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        messages.error(request, f'Could not create event: {e}')
    return redirect('outlook_calendar')
