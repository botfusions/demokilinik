"""Ajanın randevu araçları — 7 araçla sınırlı, kabuk yetkisi yok.

Eskiden `scripts/klinik-mcp.py` bu tabloyu bir stdio/JSON-RPC (MCP) sunucusu
olarak Hermes CLI'ye servis ediyordu. Hermes çıkınca protokole gerek kalmadı:
ajan (`app/ajan.py`) artık bu modülü doğrudan Python fonksiyonu olarak
çağırıyor. İş mantığı hâlâ burada değil — her araç mevcut `/api/*` uçlarını
çağırır, iki yerde çakışma kuralı olsaydı biri diğerinden saparıdı.
"""

import os

import httpx

TABAN = os.environ.get("KLINIK_API_URL", "http://localhost:8000")
ANAHTAR = os.environ.get("IC_API_ANAHTARI", "")
ZAMAN_ASIMI = float(os.environ.get("MCP_ZAMAN_ASIMI", "20"))

# Araç tablosu: ad → (metot, yol, açıklama, parametre şeması, zorunlu alanlar).
# Yol içindeki {randevu_id} çağrı argümanından doldurulur; GET'te kalan
# argümanlar query string, POST'ta gövde olur.
#
# Şemalar bilerek kısa: her araç şeması her istekte input maliyetine giriyor.
_TEL = {"type": "string", "description": "Hastanın telefonu, 905321112233 biçiminde"}

ARACLAR = {
    "doktorlari_getir": (
        "GET", "/api/doktorlar",
        "Aktif hekimleri ve hastanın daha önce gittiği hekimi döner.",
        {"telefon": _TEL},
        [],
    ),
    "gun_uygunlugu": (
        "GET", "/api/uygunluk",
        "Bir günün dolu aralıklarını ve çalışma saatlerini döner. "
        "Saat teyit etmeden önce mutlaka çağır.",
        {"gun": {"type": "string", "description": "YYYY-AA-GG"},
         "doktor_id": {"type": "integer", "description": "Belirli bir hekim için"}},
        ["gun"],
    ),
    "en_erken_musait": (
        "GET", "/api/en-erken",
        "En erken müsait aralığı ve o saatte en boş hekimi döner. Acil vakada kullan.",
        {"sure_dk": {"type": "integer", "description": "İşlem süresi, varsayılan 30"},
         "doktor_id": {"type": "integer"}},
        [],
    ),
    "randevu_olustur": (
        "POST", "/api/randevu",
        "Randevuyu yazar. Doktor verilmezse sistem o saatte en boş hekime dağıtır. "
        "Bu araç başarılı dönmeden hastaya 'oluşturdum' deme.",
        {"telefon": _TEL,
         "ad": {"type": "string", "description": "Hastanın adı"},
         "hizmet": {"type": "string"},
         "baslangic": {"type": "string", "description": "YYYY-AA-GGTSS:DD"},
         "bitis": {"type": "string", "description": "YYYY-AA-GGTSS:DD"},
         "doktor_id": {"type": "integer", "description": "Hasta 'farketmez' dediyse gönderme"},
         "notlar": {"type": "string"},
         "acil": {"type": "boolean"}},
        ["telefon", "hizmet", "baslangic", "bitis"],
    ),
    "hasta_randevulari": (
        "GET", "/api/hasta-randevulari",
        "Hastanın gelecek randevuları.",
        {"telefon": _TEL},
        ["telefon"],
    ),
    "randevu_onayla": (
        "POST", "/api/randevu/{randevu_id}/onayla",
        "Hatırlatmaya 'evet' diyen hastanın randevusunu onaylar.",
        {"randevu_id": {"type": "integer"}, "telefon": _TEL},
        ["randevu_id", "telefon"],
    ),
    "randevu_iptal": (
        "POST", "/api/randevu/{randevu_id}/iptal",
        "Randevuyu iptal eder. Telefon zorunlu — ajan yalnız konuştuğu hastanın "
        "randevusunu düşürebilsin.",
        {"randevu_id": {"type": "integer"}, "telefon": _TEL},
        ["randevu_id", "telefon"],
    ),
}


def arac_listesi_openai() -> list[dict]:
    """OpenAI function-calling şeması — `tools=` parametresine doğrudan verilir."""
    return [
        {
            "type": "function",
            "function": {
                "name": ad,
                "description": aciklama,
                "parameters": {"type": "object", "properties": ozellikler, "required": zorunlu},
            },
        }
        for ad, (_, _, aciklama, ozellikler, zorunlu) in ARACLAR.items()
    ]


def arac_calistir(ad: str, argumanlar: dict) -> tuple[str, bool]:
    """(metin, hata_mi) döner. Ajan hatayı görmeli — 409'u başarı sanıp
    hastaya 'randevunuz hazır' derse iki hasta aynı saate düşer."""
    if ad not in ARACLAR:
        return f"Bilinmeyen araç: {ad}", True

    metot, yol, *_ = ARACLAR[ad]
    argumanlar = dict(argumanlar or {})

    # Yoldaki yer tutucular argümanlardan çıkarılır, kalanı query/gövde olur
    if "{randevu_id}" in yol:
        if "randevu_id" not in argumanlar:
            return "randevu_id zorunlu", True
        yol = yol.replace("{randevu_id}", str(argumanlar.pop("randevu_id")))

    # None'lar düşer: doktor_id=None göndermek "hekim farketmez"i bozardı
    argumanlar = {k: v for k, v in argumanlar.items() if v is not None}

    try:
        yanit = httpx.request(
            metot, TABAN + yol,
            headers={"X-Ic-Anahtar": ANAHTAR},
            params=argumanlar if metot == "GET" else None,
            json=argumanlar if metot == "POST" else None,
            timeout=ZAMAN_ASIMI,
        )
    except httpx.HTTPError as e:
        return f"Klinik sistemine ulaşılamadı: {e}", True

    if yanit.status_code >= 400:
        try:
            ayrinti = yanit.json().get("detail", yanit.text)
        except ValueError:
            ayrinti = yanit.text
        return f"HATA {yanit.status_code}: {ayrinti}", True

    return yanit.text, False
