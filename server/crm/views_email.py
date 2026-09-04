"""
Per-user email over SMTP (send) + IMAP (receive) — works with GoDaddy Workspace
email or any standard mailbox. No Azure/OAuth needed. Passwords are stored
encrypted (EmailAccount). Everything happens inside the CRM.
"""
import email as _email
import imaplib
import smtplib
from email.header import decode_header, make_header
from email.mime.text import MIMEText
from email.utils import parseaddr, formatdate

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.html import escape
from django.views.decorators.http import require_POST

from .models import EmailAccount
from .views import _audit_event

# common IMAP folder names for "Sent" across providers
_SENT_NAMES = ['Sent', 'Sent Items', 'INBOX.Sent', '"Sent Items"', 'Sent Messages']


def _acct(user):
    return EmailAccount.objects.filter(user=user).first()


def _dec(s):
    try:
        return str(make_header(decode_header(s or '')))
    except Exception:
        return s or ''


def _avatar_color(s):
    h = 0
    for ch in (s or ''):
        h = (h * 31 + ord(ch)) % 360
    return f'hsl({h},52%,45%)'


# ---- settings ---------------------------------------------------------------
@login_required
@require_POST
def email_settings_save(request):
    acct, _ = EmailAccount.objects.get_or_create(user=request.user)
    acct.email = request.POST.get('email', '').strip()
    acct.smtp_host = request.POST.get('smtp_host', 'smtpout.secureserver.net').strip()
    acct.smtp_port = int(request.POST.get('smtp_port') or 465)
    acct.imap_host = request.POST.get('imap_host', 'imap.secureserver.net').strip()
    acct.imap_port = int(request.POST.get('imap_port') or 993)
    pw = request.POST.get('password', '')
    if pw:                                    # only overwrite when a new password is typed
        acct.set_password(pw)
    acct.active = True
    acct.save()
    _audit_event(request, 'Email account configured', acct.email)
    messages.success(request, 'Email settings saved.')
    return redirect('email_inbox')


@login_required
@require_POST
def email_disconnect(request):
    EmailAccount.objects.filter(user=request.user).delete()
    messages.success(request, 'Email disconnected.')
    return redirect('email_inbox')


# ---- SMTP send --------------------------------------------------------------
def _build_message(acct, to_list, subject, body_text):
    msg = MIMEText(body_text, 'plain', 'utf-8')
    msg['Subject'] = subject
    msg['From'] = acct.email
    msg['To'] = ', '.join(to_list)
    msg['Date'] = formatdate(localtime=True)
    return msg


def _smtp_send(acct, to_list, msg):
    pwd = acct.get_password()
    if int(acct.smtp_port) == 465:
        server = smtplib.SMTP_SSL(acct.smtp_host, int(acct.smtp_port), timeout=20)
    else:
        server = smtplib.SMTP(acct.smtp_host, int(acct.smtp_port), timeout=20)
        server.starttls()
    try:
        server.login(acct.email, pwd)
        server.sendmail(acct.email, to_list, msg.as_string())
    finally:
        server.quit()


_SENT_FOLDER = {}   # user_id -> the Sent folder name that worked (skip re-probing next time)


def _save_to_sent(acct, uid_key, msg):
    """Copy a just-sent message into the mailbox's Sent folder (SMTP doesn't do this).
    Uses its own short-lived connection so it can run in the background without
    blocking the pooled connection the inbox uses."""
    import time as _time
    try:
        m = _imap(acct)
    except Exception:
        return
    try:
        stamp = imaplib.Time2Internaldate(_time.time())
        raw = msg.as_bytes()
        cands = ([_SENT_FOLDER[uid_key]] if uid_key in _SENT_FOLDER else []) \
            + ['Sent', 'Sent Items', 'INBOX.Sent', 'Sent Messages']
        for cand in cands:
            try:
                typ, _r = m.append(cand, '(\\Seen)', stamp, raw)
                if typ == 'OK':
                    _SENT_FOLDER[uid_key] = cand
                    return
            except Exception:
                continue
    finally:
        try:
            m.logout()
        except Exception:
            pass


def _save_to_sent_bg(acct, uid_key, msg):
    _threading.Thread(target=_save_to_sent, args=(acct, uid_key, msg), daemon=True).start()


@login_required
@require_POST
def email_send(request):
    acct = _acct(request.user)
    if not acct or not acct.password_enc:
        messages.error(request, 'Set up your email first.')
        return redirect('email_inbox')
    to = [a.strip() for a in request.POST.get('to', '').replace(';', ',').split(',') if a.strip()]
    if not to:
        messages.error(request, 'Add at least one recipient.')
        return redirect(request.POST.get('next') or 'email_inbox')
    try:
        msg = _build_message(acct, to, request.POST.get('subject', '').strip(),
                             request.POST.get('body', ''))
        _smtp_send(acct, to, msg)
        _save_to_sent_bg(acct, request.user.id, msg)   # copy to Sent in the background
        _audit_event(request, 'Email sent', ', '.join(to)[:250])
        messages.success(request, f'Email sent to {", ".join(to)}.')
    except Exception as e:
        messages.error(request, f'Send failed: {e}')
    return redirect(request.POST.get('next') or 'email_inbox')


@login_required
@require_POST
def email_test(request):
    acct = _acct(request.user)
    if not acct or not acct.password_enc:
        messages.error(request, 'Save your email settings first.')
        return redirect('email_inbox')
    try:
        _smtp_send(acct, [acct.email], _build_message(
            acct, [acct.email], 'BITAR CRM — test email',
            'This is a test email sent from the CRM. Your email is connected correctly.'))
        messages.success(request, f'Test email sent to {acct.email}. Check your inbox.')
    except Exception as e:
        messages.error(request, f'Test failed: {e}')
    return redirect('email_inbox')


# ---- IMAP receive -----------------------------------------------------------
import threading as _threading

# One reusable IMAP connection per user + a per-user lock so we don't pay the
# ~1-2s SSL login on every click, and never share a connection concurrently.
_POOL = {}
_LOCKS = {}
_GUARD = _threading.Lock()


def _user_lock(uid):
    with _GUARD:
        lk = _LOCKS.get(uid)
        if lk is None:
            lk = _threading.Lock()
            _LOCKS[uid] = lk
        return lk


def _imap(acct):
    m = imaplib.IMAP4_SSL(acct.imap_host, int(acct.imap_port))
    m.login(acct.email, acct.get_password())
    return m


def _pool_conn(acct, uid):
    """A live pooled IMAP connection for this user (reused across requests)."""
    conn = _POOL.get(uid)
    if conn is not None:
        try:
            conn.noop()
            return conn
        except Exception:
            _drop_conn(uid)
    conn = _imap(acct)
    _POOL[uid] = conn
    return conn


def _drop_conn(uid):
    conn = _POOL.pop(uid, None)
    if conn is not None:
        try:
            conn.logout()
        except Exception:
            pass


def _select_folder(m, folder, readonly=True):
    """Select INBOX or the best-matching Sent folder. Returns the name actually selected."""
    if folder == 'sent':
        for nm in _SENT_NAMES:
            try:
                typ, _ = m.select(nm, readonly=readonly)
                if typ == 'OK':
                    return nm
            except Exception:
                continue
        m.select('INBOX', readonly=readonly)
        return 'INBOX'
    m.select('INBOX', readonly=readonly)
    return 'INBOX'


def _fetch_folder(acct, uid_key, folder='inbox', limit=40):
    """Latest messages (headers only) from a folder, newest first. Uses the pooled connection."""
    with _user_lock(uid_key):
        try:
            m = _pool_conn(acct, uid_key)
            _select_folder(m, folder)
            typ, data = m.uid('search', None, 'ALL')
            uids = (data[0].split() if data and data[0] else [])[-limit:][::-1]
            out = []
            for uid in uids:
                typ, md = m.uid('fetch', uid, '(FLAGS BODY.PEEK[HEADER.FIELDS (FROM TO SUBJECT DATE)])')
                if typ != 'OK' or not md or not isinstance(md[0], tuple):
                    continue
                flags = md[0][0].decode(errors='ignore') if md[0][0] else ''
                hdr = _email.message_from_bytes(md[0][1])
                show = hdr.get('To', '') if folder == 'sent' else hdr.get('From', '')
                who = parseaddr(show)
                name = _dec(who[0]) or who[1] or '?'
                out.append({
                    'uid': uid.decode(),
                    'from_name': name,
                    'from_addr': who[1],
                    'subject': _dec(hdr.get('Subject', '(no subject)')),
                    'date': _dec(hdr.get('Date', ''))[:31],
                    'seen': '\\Seen' in flags,
                    'initial': (name.strip()[:1] or '?').upper(),
                    'color': _avatar_color(name),
                })
            return out
        except Exception:
            _drop_conn(uid_key)          # stale connection — drop so next call reconnects
            raise


def _extract_body(msg):
    """Return (html, is_html). Prefer text/html, fall back to escaped text/plain."""
    html = text = ''
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            disp = str(part.get('Content-Disposition') or '')
            if 'attachment' in disp:
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or 'utf-8'
                decoded = payload.decode(charset, errors='replace')
            except Exception:
                continue
            if ctype == 'text/html' and not html:
                html = decoded
            elif ctype == 'text/plain' and not text:
                text = decoded
    else:
        try:
            decoded = (msg.get_payload(decode=True) or b'').decode(
                msg.get_content_charset() or 'utf-8', errors='replace')
        except Exception:
            decoded = ''
        if msg.get_content_type() == 'text/html':
            html = decoded
        else:
            text = decoded
    if html:
        return html, True
    return '<pre style="white-space:pre-wrap;font:inherit;margin:0">' + escape(text) + '</pre>', False


@login_required
def email_message(request):
    """Return one message's full body as JSON (loaded into the reading pane).
    Bodies are cached so re-opening a message is instant."""
    from django.core.cache import cache
    acct = _acct(request.user)
    uid = request.GET.get('uid', '')
    folder = request.GET.get('folder', 'inbox')
    if not acct or not acct.password_enc or not uid:
        return JsonResponse({'ok': False, 'error': 'not available'}, status=400)
    ckey = f'email:{request.user.id}:{folder}:{uid}'
    cached = cache.get(ckey)
    if cached:
        return JsonResponse(cached)
    with _user_lock(request.user.id):
        try:
            m = _pool_conn(acct, request.user.id)
            _select_folder(m, folder, readonly=False)      # writable so we can mark it read
            typ, md = m.uid('fetch', uid.encode(), '(RFC822)')
            if typ != 'OK' or not md or not isinstance(md[0], tuple):
                return JsonResponse({'ok': False, 'error': 'not found'}, status=404)
            msg = _email.message_from_bytes(md[0][1])
            # opening a message marks it as read (like Gmail)
            try:
                m.uid('store', uid.encode(), '+FLAGS', '(\\Seen)')
            except Exception:
                pass
        except Exception:
            _drop_conn(request.user.id)
            return JsonResponse({'ok': False, 'error': 'fetch failed'}, status=500)
    body, is_html = _extract_body(msg)
    frm = parseaddr(msg.get('From', ''))
    payload = {
        'ok': True,
        'from_name': _dec(frm[0]) or frm[1], 'from_addr': frm[1],
        'to': _dec(msg.get('To', '')), 'subject': _dec(msg.get('Subject', '(no subject)')),
        'date': _dec(msg.get('Date', '')), 'body': body, 'is_html': is_html,
    }
    cache.set(ckey, payload, 600)      # 10 min
    return JsonResponse(payload)


@login_required
@require_POST
def email_flag(request):
    """Mark a message read/unread (toggle the \\Seen flag)."""
    acct = _acct(request.user)
    uid = request.POST.get('uid', '')
    seen = request.POST.get('seen') == '1'
    if not acct or not acct.password_enc or not uid:
        return JsonResponse({'ok': False}, status=400)
    with _user_lock(request.user.id):
        try:
            m = _pool_conn(acct, request.user.id)
            m.select('INBOX')
            m.uid('store', uid.encode(), '+FLAGS' if seen else '-FLAGS', '(\\Seen)')
            return JsonResponse({'ok': True})
        except Exception:
            _drop_conn(request.user.id)
            return JsonResponse({'ok': False}, status=500)


@login_required
@require_POST
def email_delete(request):
    """Delete a message (move to Trash where possible, else mark deleted + expunge)."""
    acct = _acct(request.user)
    uid = request.POST.get('uid', '')
    if not acct or not acct.password_enc or not uid:
        messages.error(request, 'Could not delete.')
        return redirect('email_inbox')
    with _user_lock(request.user.id):
        try:
            m = _pool_conn(acct, request.user.id)
            m.select('INBOX')
            moved = False
            for trash in ['Trash', 'Deleted', 'Deleted Items', 'INBOX.Trash']:
                try:
                    typ, _ = m.uid('copy', uid.encode(), trash)
                    if typ == 'OK':
                        moved = True
                        break
                except Exception:
                    continue
            m.uid('store', uid.encode(), '+FLAGS', '(\\Deleted)')
            m.expunge()
            messages.success(request, 'Message deleted.' if moved else 'Message removed from inbox.')
        except Exception as e:
            _drop_conn(request.user.id)
            messages.error(request, f'Delete failed: {e}')
    from django.core.cache import cache
    cache.delete(_list_cache_key(request.user.id, 'inbox'))
    return redirect('email_inbox')


def _list_cache_key(user_id, folder):
    return f'emaillist:{user_id}:{folder}'


@login_required
def email_inbox(request):
    from django.core.cache import cache
    acct = _acct(request.user)
    folder = request.GET.get('folder', 'inbox')
    ctx = {'active_nav': 'Email', 'acct': acct, 'folder': folder,
           'connected': bool(acct and acct.password_enc), 'messages_list': [], 'error': ''}
    if ctx['connected']:
        ckey = _list_cache_key(request.user.id, folder)
        cached = None if request.GET.get('refresh') else cache.get(ckey)
        if cached is not None:
            ctx['messages_list'] = cached
        else:
            try:
                ctx['messages_list'] = _fetch_folder(acct, request.user.id, folder)
                cache.set(ckey, ctx['messages_list'], 45)     # 45s — Refresh forces reload
                acct.last_sync = timezone.now()
                acct.save(update_fields=['last_sync'])
            except imaplib.IMAP4.error:
                ctx['error'] = 'Could not sign in to your mailbox — check the email/password.'
            except Exception:
                ctx['error'] = 'Could not reach the mail server right now.'
        ctx['unseen_count'] = sum(1 for m in ctx['messages_list'] if not m['seen'])
    return render(request, 'crm/email.html', ctx)
