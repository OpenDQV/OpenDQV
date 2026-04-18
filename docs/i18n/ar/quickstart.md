# البدء السريع مع OpenDQV — من الصفر إلى عقد بيانات عامل في 90 ثانية

**المستوى:** مبتدئ | **الوقت المقدّر:** 90 ثانية

---

## المتطلبات الأساسية

- Python 3.10+ أو Docker
- اتصال بالإنترنت (لتنزيل الصورة)

---

## الخطوة 1: تشغيل OpenDQV

```bash
# باستخدام Docker (الأسهل)
docker run -p 8000:8000 opendqv/opendqv:latest

# أو باستخدام Python مباشرة
pip install opendqv
opendqv serve
```

بعد بضع ثوانٍ، ستظهر الرسالة:
```
OpenDQV v2.2.5 listening on http://localhost:8000
```

---

## الخطوة 2: كتابة أوّل عقد بيانات (contract)

العقد هو ملف YAML يصف القواعد التي يجب على البيانات الالتزام بها.

```yaml
# customer.yaml
contract:
  name: customer
  version: "1.0"
  description: بيانات العميل الأساسية
  owner: فريق-البيانات@شركة.com
  rules:
    - name: email_format
      type: regex
      field: email
      pattern: "^[^@]+@[^@]+\\.[^@]+$"
      error_message: "صيغة البريد الإلكتروني غير صحيحة"

    - name: age_range
      type: range
      field: age
      min: 18
      max: 120
      error_message: "يجب أن يكون العمر بين 18 و120"

    - name: name_not_empty
      type: not_empty
      field: name
      error_message: "الاسم مطلوب"
```

---

## الخطوة 3: التحقق من سجل

```bash
curl -X POST http://localhost:8000/api/v1/validate \
  -H "Content-Type: application/json" \
  -d '{
    "record": {"email": "alice@example.com", "age": 25, "name": "أليس"},
    "contract": "customer"
  }'
```

**الاستجابة (سجل صحيح):**
```json
{
  "valid": true,
  "errors": [],
  "warnings": [],
  "contract": "customer",
  "version": "1.0",
  "engine_version": "2.2.5"
}
```

**الاستجابة (سجل خاطئ):**
```json
{
  "valid": false,
  "errors": [
    {
      "field": "email",
      "rule": "email_format",
      "message": "صيغة البريد الإلكتروني غير صحيحة",
      "severity": "error"
    }
  ],
  "warnings": []
}
```

---

## الخطوة 4: استخدام SDK بايثون

```python
from opendqv.sdk.client import OpenDQVClient

# إنشاء العميل
client = OpenDQVClient("http://localhost:8000", token="pat_...")

# التحقق من سجل واحد
result = client.validate(
    {"email": "alice@example.com", "age": 25, "name": "أليس"},
    contract="customer",
)

if result["valid"]:
    print("✓ السجل صحيح")
else:
    for err in result["errors"]:
        print(f"✗ الحقل '{err['field']}': {err['message']}")
```

---

## المفاهيم الأساسية

| المصطلح بالإنجليزية | المصطلح بالعربية | الوصف |
|---|---|---|
| contract | عقد | ملف YAML يحدد قواعد جودة البيانات |
| rule | قاعدة | شرط محدد يجب على الحقل الوفاء به |
| record | سجل | صف بيانات يُراد التحقق منه |
| severity: error | خطورة: خطأ | فشل يوقف قبول السجل |
| severity: warning | خطورة: تحذير | مشكلة جودة لا توقف القبول |
| context | سياق | مجموعة قواعد بديلة لاستخدام محدد |

---

## الخطوات التالية

- [تأليف العقود المتقدمة](contract_authoring.md) — السياقات، الحقول الحساسة، دورة الحياة
- [مرجع API](api_reference.md) — جميع نقاط النهاية
- [SDK بايثون](../../sdk/) — الدليل الكامل للمطورين
- [حالات الاستخدام](../case_studies.md) — أمثلة من الصناعة

---

*ترجمة مجتمعية — للإبلاغ عن أخطاء أو الإسهام في تحسين الترجمة، يرجى فتح issue في GitHub.*
