from django.apps import AppConfig


class CrmConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'crm'

    def ready(self):
        from django.contrib.auth.signals import user_logged_in, user_login_failed

        def _ip(request):
            if not request:
                return ''
            xff = request.META.get('HTTP_X_FORWARDED_FOR')
            return (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')) or ''

        def on_login(sender, request, user, **kw):
            from .models import AuditEvent
            AuditEvent.objects.create(user=user, action='Login', ip=_ip(request))

        def on_login_failed(sender, credentials, request=None, **kw):
            from .models import AuditEvent
            AuditEvent.objects.create(action='Login failed',
                                      detail=credentials.get('username', ''), ip=_ip(request))

        user_logged_in.connect(on_login, dispatch_uid='crm_login_audit')
        user_login_failed.connect(on_login_failed, dispatch_uid='crm_loginfail_audit')
