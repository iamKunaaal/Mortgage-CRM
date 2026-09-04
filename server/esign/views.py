import base64
import io

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, Http404
from django.shortcuts import render, redirect, get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .models import SignRequest, SignDocument


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
        files = request.FILES.getlist('document')
        if not (title and files):
            messages.error(request, 'Title and at least one PDF document are required.')
        else:
            sr = SignRequest.objects.create(title=title, document=files[0], status='Draft',
                                            created_by=request.user)
            for i, f in enumerate(files):
                SignDocument.objects.create(request=sr, document=f, name=f.name, order=i)
            return redirect('esign_prepare', pk=sr.pk)
    return render(request, 'esign/form.html', {'active_nav': 'eSign'})


@login_required
def esign_prepare(request, pk):
    """Place signature boxes across one or more PDFs, then send to the customer."""
    import json
    sr = get_object_or_404(SignRequest, pk=pk)
    docs = [{'pk': d.pk, 'url': d.document.url, 'name': d.name or d.document.name,
             'fields': d.fields or []} for d in sr.all_docs]
    return render(request, 'esign/prepare.html',
                  {'sr': sr, 'docs_json': json.dumps(docs), 'active_nav': 'eSign'})


@login_required
@require_POST
def esign_send(request, pk):
    import json
    sr = get_object_or_404(SignRequest, pk=pk)
    try:
        raw = json.loads(request.POST.get('fields', '[]'))
    except (ValueError, TypeError):
        raw = []
    # group placed boxes by document id
    by_doc = {}
    for f in raw:
        try:
            box = {'page': int(f['page']), 'x': float(f['x']), 'y': float(f['y']),
                   'w': float(f.get('w', 24)), 'h': float(f.get('h', 9))}
        except (ValueError, KeyError, TypeError):
            continue
        by_doc.setdefault(str(f.get('doc', '')), []).append(box)
    if not any(by_doc.values()):
        messages.error(request, 'Place at least one signature box on a document first.')
        return redirect('esign_prepare', pk=pk)
    first_fields = []
    for d in sr.docs.all():
        d.fields = by_doc.get(str(d.pk), [])
        d.save(update_fields=['fields'])
        if not first_fields and d.fields:
            first_fields = d.fields
    sr.fields = first_fields                      # legacy fallback (first doc)
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
    import json
    sr = get_object_or_404(SignRequest, token=token)
    docs = [{'pk': d.pk, 'url': d.document.url, 'name': d.name or d.document.name,
             'fields': d.fields or []} for d in sr.all_docs]
    return render(request, 'esign/signer.html', {'sr': sr, 'docs_json': json.dumps(docs)})


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
def _draw_sig_boxes(c, sig, fields, pw, ph, page_no):
    """Draw the signature image at each field box on a reportlab canvas page."""
    for f in fields:
        if int(f.get('page', 1)) != page_no:
            continue
        bw = (f['w'] / 100.0) * pw
        bh = (f['h'] / 100.0) * ph
        bx = (f['x'] / 100.0) * pw
        by = ph - (f['y'] / 100.0) * ph - bh   # browser y% from top; PDF origin bottom-left
        c.drawImage(sig, bx, by, width=bw, height=bh, mask='auto', preserveAspectRatio=True)


def _stamp_pdf(docs, signature_data):
    """Stamp the signature onto each document's placed boxes; return merged PDF bytes.
    Handles both PDF documents and image documents (jpg/png/…)."""
    from pypdf import PdfReader, PdfWriter
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    b64 = signature_data.split(',', 1)[-1]
    sig = ImageReader(io.BytesIO(base64.b64decode(b64)))
    writer = PdfWriter()
    for d in docs:
        name = (d.document.name or '').lower()
        d.document.open('rb')
        raw = d.document.read()
        fields = d.fields or []
        if name.endswith('.pdf'):
            reader = PdfReader(io.BytesIO(raw))
            for i, page in enumerate(reader.pages):
                page_fields = [f for f in fields if int(f.get('page', 1)) - 1 == i]
                if page_fields:
                    pw = float(page.mediabox.width)
                    ph = float(page.mediabox.height)
                    buf = io.BytesIO()
                    c = canvas.Canvas(buf, pagesize=(pw, ph))
                    _draw_sig_boxes(c, sig, page_fields, pw, ph, i + 1)
                    c.save()
                    buf.seek(0)
                    page.merge_page(PdfReader(buf).pages[0])
                writer.add_page(page)
        else:
            # image document → render it as a single PDF page and stamp the signature on it
            bg = ImageReader(io.BytesIO(raw))
            pw, ph = bg.getSize()
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(pw, ph))
            c.drawImage(bg, 0, 0, width=pw, height=ph)
            _draw_sig_boxes(c, sig, fields, pw, ph, 1)
            c.save()
            buf.seek(0)
            writer.add_page(PdfReader(buf).pages[0])
    out = io.BytesIO()
    writer.write(out)
    out.seek(0)
    return out.read()


def _download_signed(request, sr, docs, filename):
    if sr.status != 'Signed' or not sr.signature_data:
        raise Http404('Not signed yet')
    if not docs:
        raise Http404('No document')
    try:
        data = _stamp_pdf(docs, sr.signature_data)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        if request.GET.get('debug'):
            return HttpResponse(f'e-Sign stamp error: {ex!r}', content_type='text/plain', status=500)
        return redirect(docs[0].document.url)
    resp = HttpResponse(data, content_type='application/pdf')
    resp['Content-Disposition'] = f'attachment; filename="{filename}"'
    return resp


def esign_download(request, token):
    """Download all documents in the request as one merged signed PDF."""
    sr = get_object_or_404(SignRequest, token=token)
    fn = (sr.title or 'document').replace(' ', '_') + '_signed.pdf'
    return _download_signed(request, sr, sr.all_docs, fn)


def esign_download_doc(request, token, doc_id):
    """Download ONE document from the request as its own signed PDF."""
    sr = get_object_or_404(SignRequest, token=token)
    d = get_object_or_404(SignDocument, pk=doc_id, request=sr)
    base = (d.name or f'document_{d.pk}').rsplit('.', 1)[0].replace(' ', '_')
    return _download_signed(request, sr, [d], base + '_signed.pdf')


@login_required
@require_POST
def esign_delete(request, pk):
    sr = get_object_or_404(SignRequest, pk=pk)
    title = sr.title
    sr.delete()
    messages.success(request, f'"{title}" deleted.')
    return redirect('esign_list')
