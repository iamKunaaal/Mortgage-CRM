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
        return bool(self.fields) or any(d.fields for d in self.docs.all())

    @property
    def all_docs(self):
        """Every document in this request as SignDocument rows. Falls back to the
        legacy single `document` field for old requests created before multi-doc."""
        rows = list(self.docs.all())
        if rows:
            return rows
        if self.document:
            return [SignDocument(request=self, document=self.document,
                                 name=self.title, fields=self.fields, order=0)]
        return []

    def __str__(self):
        return f'{self.title} · {self.status}'


class SignDocument(models.Model):
    """One document within a SignRequest (a request can bundle several PDFs)."""
    request = models.ForeignKey(SignRequest, on_delete=models.CASCADE, related_name='docs')
    document = models.FileField(upload_to='esign/')
    name = models.CharField(max_length=200, blank=True)
    fields = models.JSONField(default=list, blank=True)   # sig boxes for THIS document
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order', 'id']

    def __str__(self):
        return self.name or self.document.name
