"""LLM'siz kural katmanı — hafif yoldan da önce çalışır.

Hedef: "100 mesajın 100'ü LLM'ye" değil, "10-20'si LLM'ye". Bilgi tabanında
zaten yazılı bir soruya (fiyat, çalışma saati, adres) ya da hatırlatmaya
tek kelimelik bir cevaba (evet/iptal) LLM hiç çağrılmadan yanıt verilir.

İki fonksiyon da "şüphede LLM'e" yönünde: emin olunamayan her durum `None`
döner, çağıran (`app/main.py`) sırayla hafif yola, sonra tam ajana düşer.
Yanlış yönün bedeli asimetrik — bilgi sorusu LLM'e giderse yalnız pahalı
olur, randevu isteği ya da belirsiz bir hatırlatma cevabı burada yanlış
karşılanırsa hasta boşa çevrilir ya da yanlış randevu iptal olur.
"""

import json
import os
from difflib import SequenceMatcher

from app import araclar, hafif, kb

# Bilgi tabanı eşleşmesi için benzerlik eşiği. Tam eşleşme 1.0; yazım
# hatası/kısaltma farkı tipik olarak 0.75-0.9 arasına düşüyor. 0.72
# muhafazakâr taraf: emin değilsem hafif yola/tam ajana düş.
ESIK = float(os.environ.get("KURAL_BENZERLIK_ESIGI", "0.72"))

# Bilgi sorusu tek cümlelik olur; bundan uzun mesaj muhtemelen birden çok
# konu taşıyor, fuzzy eşleşme yanıltıcı olur.
_MAKS_UZUNLUK = 80

_ONAY_KELIMELERI = {"evet", "tamam", "olur", "geliyorum", "onaylıyorum"}
_IPTAL_KELIMELERI = {"iptal", "gelemiyorum", "gelemeyeceğim"}


def cevap_dene(conn, mesaj: str) -> str | None:
    """Bilgi tabanındaki bir başlıkla net eşleşen mesajı LLM'siz cevaplar."""
    if hafif._randevu_sinyali(mesaj) or len(mesaj) > _MAKS_UZUNLUK:
        return None

    en_iyi, skor = None, 0.0
    for satir in kb.bilgiler_listele(conn, yalniz_aktif=True):
        s = SequenceMatcher(None, mesaj.lower(), satir["baslik"].lower()).ratio()
        if s > skor:
            en_iyi, skor = satir, s

    return en_iyi["icerik"] if en_iyi and skor >= ESIK else None


def hatirlatma_cevabi_dene(telefon: str, mesaj: str) -> str | None:
    """Hatırlatmaya tek kelimelik "evet"/"iptal" cevabını LLM'siz karşılar.

    Yalnızca hastanın gelecekte **tam bir** randevusu varsa çalışır — 0 ya
    da 2+ randevuda hangisi kastedildiği belirsizdir, karar tam ajana bırakılır.
    Aynı `/api/*` uçlarını (`araclar.arac_calistir` üzerinden) çağırır ki
    yetki kontrolü (telefon randevu sahibiyle eşleşiyor mu) iki yerde ayrı
    ayrı yazılmasın.
    """
    kelime = hafif._turkce_kucult(mesaj.strip().rstrip("."))
    if kelime not in (_ONAY_KELIMELERI | _IPTAL_KELIMELERI):
        return None

    metin, hata = araclar.arac_calistir("hasta_randevulari", {"telefon": telefon})
    if hata:
        return None
    try:
        randevular = json.loads(metin).get("randevular", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    if len(randevular) != 1:
        return None
    randevu_id = randevular[0]["randevu_id"]

    if kelime in _ONAY_KELIMELERI:
        _, hata = araclar.arac_calistir(
            "randevu_onayla", {"randevu_id": randevu_id, "telefon": telefon}
        )
        return None if hata else "Teşekkürler, sizi bekliyoruz."

    _, hata = araclar.arac_calistir(
        "randevu_iptal", {"randevu_id": randevu_id, "telefon": telefon}
    )
    return None if hata else "Randevunuzu iptal ettim. Yeniden almak isterseniz yazmanız yeterli."
