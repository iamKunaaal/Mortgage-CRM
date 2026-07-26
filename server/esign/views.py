import base64
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import SignRequest


def _client_ip(request):
    xff = request.META.get('HTTP_X_FORWARDED_FOR')
    return (xff.split(',')[0].strip() if xff else request.META.get('REMOTE_ADDR', '')) or ''


# ---------------- sender side (staff, logged in) ----------------
@login_required
def esign_list(request):
    docs = SignRequest.objects.all()
    # management sees all requests; everyone else sees only the ones they created
    mgmt = request.user.is_superuser or getattr(request.user, 'role', '') in (
        'CEO', 'SUPER_ADMIN', 'OPS_MANAGER', 'SALES_DIRECTOR')
    if not mgmt:
        docs = docs.filter(created_by=request.user)
    kpis = {'total': docs.count(),
            'awaiting': docs.filter(status='Awaiting').count(),
            'signed': docs.filter(status='Signed').count(),
            'draft': docs.filter(status='Draft').count()}
    return render(request, 'esign/list.html', {'docs': docs, 'kpis': kpis, 'active_nav': 'eSign'})


@login_required
def esign_create(request):
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        f = request.FILES.get('document')
        if not (title and f):
            messages.error(request, 'Title and a PDF document are required.')
        else:
            sr = SignRequest.objects.create(title=title, document=f, status='Draft',
                                            created_by=request.user)
            return redirect('esign_prepare', pk=sr.pk)
    return render(request, 'esign/form.html', {'active_nav': 'eSign'})


@login_required
def esign_prepare(request, pk):
    """Place the signature box on the PDF, then send to the customer."""
    sr = get_object_or_404(SignRequest, pk=pk)
    return render(request, 'esign/prepare.html', {'sr': sr, 'active_nav': 'eSign'})


@login_required
@require_POST
def esign_send(request, pk):
    import json
    sr = get_object_or_404(SignRequest, pk=pk)
    try:
        raw = json.loads(request.POST.get('fields', '[]'))
        fields = []
        for f in raw:
            fields.append({'page': int(f['page']), 'x': float(f['x']), 'y': float(f['y']),
                           'w': float(f.get('w', 24)), 'h': float(f.get('h', 9))})
    except (ValueError, KeyError, TypeError):
        fields = []
    if not fields:
        messages.error(request, 'Place at least one signature box on the document first.')
        return redirect('esign_prepare', pk=pk)
    sr.fields = fields
    sr.signer_name = request.POST.get('signer_name', '').strip()
    sr.signer_email = request.POST.get('signer_email', '').strip()
    sr.status = 'Awaiting'
    sr.save()
    # email sending not enabled yet — return the shareable signing link for testing
    link = request.build_absolute_uri('/esign/s/' + str(sr.token) + '/')
    messages.success(request, 'Sent for signature. Share this link with the customer: ' + link)
    return redirect('esign_prepare', pk=pk)


# ---------------- signer side (public, no login) ----------------
def esign_public_sign(request, token):
    sr = get_object_or_404(SignRequest, token=token)
    return render(request, 'esign/signer.html', {'sr': sr})


@require_POST
def esign_public_submit(request, token):
    sr = get_object_or_404(SignRequest, token=token)
    if sr.status == 'Signed':
        return redirect('esign_public_sign', token=token)
    sig = request.POST.get('signature_data', '').strip()
    if sig:
        sr.signature_data = sig
        sr.status = 'Signed'
        sr.signed_at = timezone.now()
        sr.signed_ip = _client_ip(request)
        sr.save()
    return redirect('esign_public_sign', token=token)


@login_required
def esign_view(request, pk):
    """Show a signature request in the dashboard — including the customer's captured signature."""
    sr = get_object_or_404(SignRequest, pk=pk)
    mgmt = request.user.is_superuser or getattr(request.user, 'role', '') in (
        'CEO', 'SUPER_ADMIN', 'OPS_MANAGER', 'SALES_DIRECTOR')
    if not mgmt and sr.created_by_id != request.user.id:
        raise Http404()
    return render(request, 'esign/view.html', {'sr': sr, 'active_nav': 'eSign'})


# ---------------- signed PDF download (stamps signature at placed coords) ----------------
def esign_download(request, token):
    sr = get_object_or_404(SignRequest, token=token)
    if sr.status != 'Signed' or not sr.signature_data:
        raise Http404('Not signed yet')
    name = (sr.document.name or '').lower()
    if not name.endswith('.pdf'):
        # non-PDF: just return the original (stamping only supported for PDF)
        return redirect(sr.document.url)

    try:
        from pypdf import PdfReader, PdfWriter
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader

        sr.document.open('rb')
        reader = PdfReader(sr.document)
        writer = PdfWriter()

        b64 = sr.signature_data.split(',', 1)[-1]
        img = ImageReader(io.BytesIO(base64.b64decode(b64)))

        for i, page in enumerate(reader.pages):
            page_fields = [f for f in (sr.fields or []) if int(f.get('page', 1)) - 1 == i]
            if page_fields:
                pw = float(page.mediabox.width)
                ph = float(page.mediabox.height)
                buf = io.BytesIO()
                c = canvas.Canvas(buf, pagesize=(pw, ph))
                for f in page_fields:
                    bw = (f['w'] / 100.0) * pw
                    bh = (f['h'] / 100.0) * ph
                    bx = (f['x'] / 100.0) * pw
                    by = ph - (f['y'] / 100.0) * ph - bh   # browser y% from top; PDF origin bottom-left
                    c.drawImage(img, bx, by, width=bw, height=bh, mask='auto', preserveAspectRatio=True)
                c.save()
                buf.seek(0)
                page.merge_page(PdfReader(buf).pages[0])
            writer.add_page(page)

        out = io.BytesIO()
        writer.write(out)
        out.seek(0)
        data = out.read()
    except Exception as ex:
        # surface the real cause in server logs, then fall back to the original file
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(f'e-Sign stamp error: {ex!r}', content_type='text/plain', status=500)
        return redirect(sr.document.url)

    resp = HttpResponse(data, content_type='application/pdf')
    fn = (sr.title or 'document').replace(' ', '_') + '_signed.pdf'
    resp['Content-Disposition'] = f'attachment; filename="{fn}"'
    return resp


@login_required
@require_POST
def esign_delete(request, pk):
    sr = get_object_or_404(SignRequest, pk=pk)
    title = sr.title
    sr.delete()
    messages.success(request, f'"{title}" deleted.')
    return redirect('esign_list')
