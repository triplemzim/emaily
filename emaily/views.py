from django.http import HttpResponse, JsonResponse
from django.shortcuts import render


import csv
import io
import re
import time
import uuid
import dns.resolver
import smtplib
from tempfile import NamedTemporaryFile
from django.views.decorators.csrf import csrf_exempt


EMAIL_REGEX = re.compile(r"[^@]+@[^@]+\.[^@]+")
DISPOSABLE_DOMAINS = {"mailinator.com", "10minutemail.com", "guerrillamail.com"}
ROLE_BASED_PREFIXES = {"info", "support", "admin", "sales", "contact"}

data = {}


def index(request):
    return render(request, 'emaily/index.html')

def check_email(email):
    import time

    if not EMAIL_REGEX.match(email):
        return "invalid", "bad_syntax"

    domain = email.split('@')[1]
    local = email.split('@')[0]

    if domain.lower() in DISPOSABLE_DOMAINS:
        return "invalid", "disposable_domain"
    if local.lower() in ROLE_BASED_PREFIXES:
        return "invalid", "role_based"

    try:
        records = dns.resolver.resolve(domain, 'MX')
        mx_record = str(records[0].exchange)
    except Exception:
        return "invalid", "no_mx"

    try:
        server = smtplib.SMTP(timeout=10)
        server.connect(mx_record)
        server.helo("example.com")
        server.mail("probe@example.com")
        code, _ = server.rcpt(f"doesnotexist123@{domain}")
        server.quit()
        if code == 250:
            return "risky", "domain_accepts_all"
    except Exception:
        pass

    def smtp_check():
        try:
            server = smtplib.SMTP(timeout=20)
            server.connect(mx_record)
            server.helo("example.com")
            server.mail("verifier@example.com")
            code, _ = server.rcpt(email)
            server.quit()
            return code
        except Exception:
            return None

    code = smtp_check()
    if code in [421, 450, 451, 452, 503]:
        time.sleep(5)
        code = smtp_check()

    if code == 250:
        return "valid", "smtp_ok"
    elif code is None:
        return "risky", "smtp_timeout"
    elif code in [421, 450, 451, 452, 503]:
        return "risky", f"smtp_soft_fail_{code}"
    elif code == 550:
        return "invalid", "smtp_reject"
    else:
        return "invalid", f"smtp_{code}"

@csrf_exempt
def verify(request):
    if request.method != 'POST':
        return "Invalid request method", 400

    job_id = str(uuid.uuid4())

    file = request.FILES.get('file')
    content = file.read().decode('utf-8')
    reader = list(csv.DictReader(io.StringIO(content)))
    total = len(reader)
    email_field = next((f for f in reader[0].keys() if f.lower().strip() == 'email'), None)

    output = io.StringIO()
    fieldnames = list(reader[0].keys()) + ['status', 'reason']
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()

    data[job_id] = {
        "progress": 0,
        "row": 0,
        "total": total,
        "log": "",
        "cancel": False,
        "output": output,
        "writer": writer,
        "records": reader,
        "email_field": email_field,
        "filename": file.name
    }

    def run():
        for i, row in enumerate(reader, start=1):
            if data[job_id]['cancel']:
                data[job_id]['log'] = f"\u274c Canceled job {job_id}"
                break
            email = (row.get(email_field) or '').strip()
            if not email:
                status, reason = 'invalid', 'empty_email'
            else:
                status, reason = check_email(email)
            row['status'], row['reason'] = status, reason
            writer.writerow(row)
            percent = int((i / total) * 100)
            data[job_id].update({"progress": percent, "row": i,
                                 "log": f"\u2705 {email} → {status} ({reason})"})
        output = data[job_id]['output']
        output.seek(0)
        temp = NamedTemporaryFile(delete=False, suffix=".csv", mode='w+')
        temp.write(output.read())
        temp.flush()
        temp.seek(0)
        data[job_id]['file_path'] = temp.name

    import threading
    threading.Thread(target=run).start()

    return JsonResponse({"job_id": job_id})


def progress(request):
    job_id = request.GET.get("job_id")
    d = data.get(job_id, {})
    return JsonResponse({"percent": d.get("progress", 0), "row": d.get("row", 0), "total": d.get("total", 0)})

def log(request):
    job_id = request.GET.get("job_id")
    return HttpResponse(data.get(job_id, {}).get("log", ""), content_type="text/plain")

def cancel(request):
    job_id = request.GET.get("job_id")
    if job_id in data:
        data[job_id]['cancel'] = True
    return '', 204

def download(request):
    job_id = request.GET.get("job_id")
    filter_type = request.GET.get("type", "all")
    job = data.get(job_id)
    if not job:
        return "Invalid job ID", 404

    job['output'].seek(0)
    reader = list(csv.DictReader(job['output']))

    if filter_type == "valid":
        filtered = [row for row in reader if row['status'] == 'valid']
    elif filter_type == "risky":
        filtered = [row for row in reader if row['status'] == 'risky']
    elif filter_type == "risky_invalid":
        filtered = [row for row in reader if row['status'] in ('risky', 'invalid')]
    else:
        filtered = reader

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=reader[0].keys())
    writer.writeheader()
    for row in filtered:
        writer.writerow(row)

    output.seek(0)
    download_name = f"{filter_type}-galadon-{job['filename']}"
    return HttpResponse(
        output.getvalue(),
        content_type='text/csv',
        headers={"Content-Disposition": f"attachment; filename={download_name}"}
    )
