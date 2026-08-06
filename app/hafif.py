"""Hermes'siz cevap yolu — bilgi soruları için.

Ölçüm: bir mesajın 28,5 KB'lık prompt'unun %78'i Hermes'in kendi çerçevesi
(system prompt + araç şemaları). "İmplant ne kadar sürüyor?" sorusuna cevap
vermek için gereken her şey kalan %22'de: SOUL.md + .hermes.md. Araç çağırma
çerçevesi bilgi sorusunda hiç kullanılmıyor ama her seferinde ödeniyor.

Burası o çerçeveyi atlar: aynı kimlik, aynı bilgi tabanı, doğrudan API çağrısı.
Randevu işi Hermes'te kalır — araçlar orada.

Üç kapı, üçü de "şüphede Hermes'e" yönünde. Yanlış yönün bedeli asimetrik:
bilgi sorusu Hermes'e giderse yalnız pahalı olur, cevap yine doğrudur. Randevu
isteği buraya düşerse ajan randevu açamaz ve hastayı boşa çevirir.
"""

import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger(__name__)

PROJE_KOKU = Path(__file__).resolve().parent.parent
SOUL = PROJE_KOKU / "hermes-home" / "SOUL.md"
HERMES_MD = PROJE_KOKU / ".hermes.md"

# ZAI'de iki uç nokta var ve abonelik türü hangisinin çalıştığını belirliyor:
# bu kurulumun coding planı `/api/paas/v4`'te "Insufficient balance" (429)
# dönüyor, `/api/coding/paas/v4` çalışıyor. Başka bir abonelikte tersi olur —
# `.env`'de `AJAN_TABAN_URL` ile ezilebilir.
TABAN_URL = {
    "zai": "https://api.z.ai/api/coding/paas/v4",
    "openai": "https://api.openai.com/v1",
}
ANAHTAR_ADI = {"zai": "ZAI_API_KEY", "openai": "OPENAI_API_KEY"}

ZAMAN_ASIMI = float(os.environ.get("HAFIF_ZAMAN_ASIMI", "30"))

# Bu kelimelerden biri geçen mesaj Hermes'e gider. Liste bilerek dar:
# "saat" ve "gün" burada YOK, çünkü "çalışma saatleriniz nedir" tam da
# buraya düşmesini istediğimiz soru. Randevu bağlamını yakalamak listeye
# değil, `_gecmiste_randevu_var` kapısına bırakıldı.
RANDEVU_SINYALLERI = (
    "randevu", "müsait", "musait", "iptal", "ertele", "erteliy",
    "geliyorum", "gelemiy", "gelemeyec", "gelebilir", "geleyim",
    "acil", "ağrı", "agri", "şişlik", "kırıl", "kiril", "düştü", "onaylıyorum",
)

# Modelin kendi devretme işareti — kapılardan sızan bir randevu isteği için
# son savunma. Cevap olarak gönderilmez.
DEVRET = "[RANDEVU]"


def _turkce_kucult(metin: str) -> str:
    """Bütün i harflerini tek biçime katlar.

    İki ayrı tuzak var. Python'un `lower()`'ı 'İ'yi birleşik noktalı 'i̇'ye
    çevirir, `"iptal" in "i̇ptal"` False döner. Türkçe kurallı dönüşüm ise
    'I'yı 'ı' yapar ve ASCII klavyeden yazılan "Iptal" kaçar — WhatsApp'ta
    çok yaygın. Sinyal aramasında ı/i ayrımı anlam taşımadığı için hepsini
    'i'ye katlıyoruz; aynı katlama sinyal listesine de uygulanıyor.
    """
    return metin.replace("İ", "i").replace("I", "i").replace("ı", "i").lower()


_SINYALLER = tuple(_turkce_kucult(s) for s in RANDEVU_SINYALLERI)


def _randevu_sinyali(metin: str) -> bool:
    kucuk = _turkce_kucult(metin)
    return any(s in kucuk for s in _SINYALLER)


def _gecmiste_randevu_var(gecmis: list[dict]) -> bool:
    """Konuşma bir kez randevuya girdiyse devamı da Hermes'te kalır.

    "Yarın 14:00 olur mu" tek başına randevu sinyali taşımıyor; bağlamı
    taşıyor. Bu kapı olmasaydı randevu konuşmasının ortası buraya düşerdi.
    """
    return any(_randevu_sinyali(g["mesaj"]) for g in gecmis)


def _sistem_promptu() -> str | None:
    """SOUL + bilgi tabanı. Biri okunamıyorsa hafif yol devre dışı — eksik
    kimlikle cevap üretmektense Hermes'e düşmek doğru."""
    try:
        soul = SOUL.read_text()
        bilgi = HERMES_MD.read_text()
    except OSError as e:
        log.warning("Hafif yol devre dışı, dosya okunamadı: %s", e)
        return None

    return (
        f"{soul}\n\n"
        f"# Klinik bilgileri\n\n{bilgi}\n\n"
        f"# Bu kanalda\n\n"
        f"Randevu araçların yok. Hasta randevu almak, değiştirmek, iptal etmek "
        f"ya da bir saat/gün teyit etmek istiyorsa tek başına {DEVRET} yaz, "
        f"başka hiçbir şey yazma — isteği yetkili sistem devralacak.\n"
        f"Diğer her şeyi yukarıdaki klinik bilgilerine göre normal cevapla."
    )


def _maliyet(kullanim: dict) -> float | None:
    """1M token fiyatı `.env`'de tanımlıysa hesaplar. Tanımlı değilse None —
    uydurulmuş bir rakam panelde gerçek maliyet gibi görünürdü."""
    try:
        giris = float(os.environ["AJAN_1M_GIRIS_USD"])
        cikis = float(os.environ["AJAN_1M_CIKIS_USD"])
    except (KeyError, ValueError):
        return None

    return round(
        kullanim.get("prompt_tokens", 0) / 1e6 * giris
        + kullanim.get("completion_tokens", 0) / 1e6 * cikis,
        6,
    )


def cevap_dene(gecmis: list[dict], mesaj: str,
               kanal: str = "whatsapp") -> tuple[str, float | None] | None:
    """(yanıt, maliyet) ya da None. None = Hermes'e devret.

    Hiçbir hata dışarı sızmaz: bu yol en iyi çaba katmanıdır, tökezlerse
    mesaj Hermes'e düşer ve hasta farkı görmez.
    """
    if os.environ.get("HAFIF_YOL", "1") == "0":
        return None
    # Instagram'da randevu zaten açılmıyor ama kanal ayrımı ajan.py'de
    # prompt'a yazılı; iki yerde iki farklı sınır tutmamak için buraya sokmuyoruz.
    if kanal != "whatsapp":
        return None

    if _randevu_sinyali(mesaj) or _gecmiste_randevu_var(gecmis):
        return None

    saglayici = os.environ.get("AJAN_PROVIDER", "")
    taban = os.environ.get("AJAN_TABAN_URL") or TABAN_URL.get(saglayici)
    anahtar = os.environ.get(ANAHTAR_ADI.get(saglayici, ""), "")
    if not taban or not anahtar:
        return None

    if (sistem := _sistem_promptu()) is None:
        return None

    mesajlar = [{"role": "system", "content": sistem}]
    for g in gecmis:
        mesajlar.append({
            "role": "user" if g["yon"] == "gelen" else "assistant",
            "content": g["mesaj"],
        })
    mesajlar.append({"role": "user", "content": mesaj})

    try:
        yanit = httpx.post(
            f"{taban}/chat/completions",
            headers={"Authorization": f"Bearer {anahtar}"},
            json={
                # Hafif yol için ayrı (ucuz) model verilebilir; verilmezse
                # ana modelin kendisi kullanılır.
                "model": os.environ.get("AJAN_HAFIF_MODEL") or os.environ.get("AJAN_MODEL"),
                "messages": mesajlar,
                "temperature": 0.3,
            },
            timeout=ZAMAN_ASIMI,
        )
        yanit.raise_for_status()
        govde = yanit.json()
        metin = govde["choices"][0]["message"]["content"].strip()
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
        log.warning("Hafif yol başarısız, Hermes'e düşülüyor: %s", e)
        return None

    if not metin or DEVRET in metin:
        return None

    return metin, _maliyet(govde.get("usage") or {})
