import uuid

from django.conf import settings
from django.db import models


class SignRequest(models.Model):
    """A document sent for signature (Odoo-Sign-style). Self-contained, removable module."""
    STATUS = [('Draft', 'Draft'), ('Awaiting', 'Awaiting Signature'), ('Signed', 'Signed')]

    token = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    title = models.CharField(max_length=160)
    document = models.FileField(upload_to='esign/')
    signer_name = models.CharField(max_length=120, blank=True)
    signer_email = models.EmailField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS, default='Draft')

    # one or more signature boxes: [{"page":1,"x":40,"y":20,"w":24,"h":9}, ...]  (percentages)
    fields = models.JSONField(default=list, blank=True)
    # legacy single-box columns (kept for back-compat, no longer used)
    sig_page = models.PositiveIntegerField(default=1)
    sig_x = models.FloatField(null=True, blank=True)
    sig_y = models.FloatField(null=True, blank=True)
    sig_w = models.FloatField(default=24)
    sig_h = models.FloatField(default=9)

    signature_data = models.TextField(blank=True)      # base64 PNG of the signature
    signed_at = models.DateTimeField(null=True, blank=True)
    signed_ip = models.CharField(max_length=64, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='sign_requests')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    @property
    def placed(self):
        return bool(self.fields)

    def __str__(self):
        return f'{self.title} · {self.status}'
