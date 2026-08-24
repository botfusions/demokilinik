"""Ajan — kimlik + randevu araçlarıyla tam yetkili tur.

Hermes CLI'ye bağımlılık yok: burası doğrudan `/chat/completions`'a
`tools=` ile giden, elle yazılmış bir tool-calling döngüsü. Oturum yönetimi
yok — konuşma geçmişinin tek kaynağı Postgres, her çağrıda mesaj listesine
konur. İki yerde state tutmak senkron sorunu demek.
"""

import json
import logging
import os

import httpx

from app import araclar, hafif

log = logging.getLogger(__name__)

# Artık bir subprocess'in tek seferlik tavanı değil, her `/chat/completions`
# çağrısı için ayrı ayrı uygulanan zaman aşımı.
ZAMAN_ASIMI = float(os.environ.get("AJAN_ZAMAN_ASIMI", "60"))

# Tool-calling döngüsünün tavanı. Modelin aynı aracı sonsuz çağırmasına karşı
# koruma; dar araç şeması ve basit resepsiyon işiyle 2-4 turda biter, 8 güvenli.
# (her çağrıda `os.environ`'dan okunur, testte ortam değişkeniyle ezilebilsin)
def _max_tur() -> int:
    return int(os.environ.get("AJAN_MAX_TUR", "8"))


class CevapUretilemedi(Exception):
    """Ajan cevap üretemedi — API hatası, zaman aşımı ya da boş çıktı."""


def _whatsapp_hatti() -> str:
    return os.environ.get("KLINIK_WHATSAPP_NUMARASI", "")


def _sistem_promptu(kanal: str) -> str:
    """Kimlik + bilgi tabanı, kanala özel talimatla. Okunamıyorsa `CevapUretilemedi`
    — eksik kimlikle cevap üretmektense hastaya "sistem hatası" demek doğru."""
    temel = hafif.kimlik_ve_bilgi()
    if temel is None:
        raise CevapUretilemedi("SOUL.md ya da .hermes.md okunamadı")

    if kanal != "instagram":
        return temel

    # Instagram bilgilendirme kanalı: randevu açma yetkisi YOK. Bu sınır burada
    # duruyor çünkü tek fark kanal; ajanın kimliği ve bilgi tabanı aynı. Ayrıca
    # bu kanalda `tools=[]` gider — model tool çağırmayı deneyip reddedilmekten
    # daha kesin bir sınır.
    hat = _whatsapp_hatti()
    if hat:
        rakam = "".join(c for c in hat if c.isdigit())
        nereye = f"WhatsApp hattımıza ({hat}, https://wa.me/{rakam})"
    else:
        nereye = "WhatsApp hattımıza"
    return temel + (
        "\n\n# Bu kanalda\n\n"
        "Bu mesaj Instagram'dan geldi. Instagram yalnızca bilgilendirme kanalıdır:\n"
        "- Soruyu bilgi tabanındaki bilgilerle cevapla (hizmet, fiyat, çalışma saatleri, adres).\n"
        f"- Randevu almak, değiştirmek veya iptal etmek isteyen kişiyi {nereye} yönlendir.\n"
        "- WhatsApp bilgisini SADECE kişi randevu istediğinde ver. Selamlamada, genel\n"
        "  soru cevabında ya da 'nasıl yardımcı olabilirim' mesajında numara/link ekleme;\n"
        "  önce kişinin derdini anla, cevabını ver, randevu gelirse o zaman yönlendir.\n"
        "- Randevu KAYDI AÇMA, saat verme, 'ayırdım/kaydettim' deme. Bu kanalda o yetkin yok.\n"
        "- Randevu araçlarını çağırma (zaten sende yok)."
    )


def _saglayici_ayarlari() -> tuple[str, str]:
    saglayici = os.environ.get("AJAN_PROVIDER", "")
    taban = os.environ.get("AJAN_TABAN_URL") or hafif.TABAN_URL.get(saglayici)
    anahtar = os.environ.get(hafif.ANAHTAR_ADI.get(saglayici, ""), "")
    if not taban or not anahtar:
        raise CevapUretilemedi(f"'{saglayici}' için taban URL/anahtar tanımsız")
    return taban, anahtar


KONUM_ISARETI = "[KONUM]"


def konum_ayikla(yanit: str) -> tuple[str, bool]:
    """Ajanın cevabından `[KONUM]` işaretini ayıklar → (temiz metin, konum_istendi).

    İşaret hastaya da personele de gösterilmez; kaydedilmeden önce burada
    düşer. Her iki kanal da (WhatsApp, Instagram) bu fonksiyondan geçiyor —
    yoksa Instagram cevaplarında ham `[KONUM]` yazısı görünürdü.
    """
    if KONUM_ISARETI not in yanit:
        return yanit, False
    return yanit.replace(KONUM_ISARETI, "").strip(), True


def klinik_konumu() -> tuple[float, float] | None:
    """`.env`'deki `KLINIK_KONUM=enlem,boylam`. Bozuk/eksikse None.

    Koordinat sabit ve elle girilir; ajan koordinat üretmez — uydurulmuş bir
    enlem/boylam hastayı yanlış adrese gönderir.
    """
    ham = os.environ.get("KLINIK_KONUM", "").strip()
    if not ham:
        return None
    try:
        enlem, boylam = (float(p) for p in ham.split(",", 1))
    except ValueError:
        return None
    if not (-90 <= enlem <= 90 and -180 <= boylam <= 180):
        return None
    return enlem, boylam


def cevap_uret(gecmis: list[dict], mesaj: str,
               kanal: str = "whatsapp", telefon: str | None = None,
               ad: str | None = None) -> tuple[str, dict]:
    """(yanıt, {"prompt_tokens":.., "completion_tokens":..}) döner. Başarısızlıkta CevapUretilemedi.

    Bilgi soruları ve tek kelimelik hatırlatma cevapları buraya hiç uğramaz —
    `app/kural.py` ve `app/hafif.py` daha ucuz katmanlarda cevap üretir. Kapı
    çağıranda (`app/main.py`) sırayla denenir; burası son ve en pahalı adım.
    """
    sistem = _sistem_promptu(kanal)
    if telefon:
        # Numara webhook'ta zaten elimizde; ajana söylemezsek araç şeması
        # `telefon`u zorunlu istediği için hastaya soruyor ("numaranızı paylaşır
        # mısınız?") — hastanın WhatsApp'tan yazdığı numarayı sormak saçma.
        sistem += (
            "\n\n# Bu hasta\n\n"
            f"Bu mesajı yazan hastanın telefonu: {telefon}\n"
            + (f"WhatsApp profil adı (tahmin, resmi kayıt değil): {ad}\n" if ad else "")
            + "Araç çağrılarında `telefon` alanına bu numarayı koy; hastadan "
            "telefon numarası İSTEME. Ad için: randevu açarken hastaya gerçek "
            "adını ve soyadını SOR — WhatsApp adı takma ad olabilir, kayda "
            "hastanın söylediği tam ad geçer (`ad` alanına onu yaz)."
        )
    taban, anahtar = _saglayici_ayarlari()

    messages = [{"role": "system", "content": sistem}]
    for g in gecmis:
        messages.append({
            "role": "user" if g["yon"] == "gelen" else "assistant",
            "content": g["mesaj"],
        })
    messages.append({"role": "user", "content": mesaj})

    tools = araclar.arac_listesi_openai() if kanal == "whatsapp" else []
    model = os.environ.get("AJAN_MODEL")
    toplam_kullanim = {"prompt_tokens": 0, "completion_tokens": 0}

    for _ in range(_max_tur()):
        try:
            yanit = httpx.post(
                f"{taban}/chat/completions",
                headers={"Authorization": f"Bearer {anahtar}"},
                json=hafif.istek_govdesi(model, messages, tools=tools or None),
                timeout=ZAMAN_ASIMI,
            )
            yanit.raise_for_status()
            govde = yanit.json()
            msg = govde["choices"][0]["message"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as e:
            # Sağlayıcının hata gövdesi olmadan 400 teşhis edilemiyor (canlıda
            # iki deploy turu buna gitti: "Unknown parameter: 'effort'" ve
            # "function tools with reasoning_effort not supported" yalnız
            # gövdede yazıyordu, durum kodunda değil).
            ayrinti = getattr(getattr(e, "response", None), "text", "")[:300]
            raise CevapUretilemedi(f"LLM çağrısı başarısız: {e} {ayrinti}") from e

        for k, v in (govde.get("usage") or {}).items():
            if k in toplam_kullanim:
                toplam_kullanim[k] += v

        tool_calls = msg.get("tool_calls")
        if not tool_calls:
            metin = (msg.get("content") or "").strip()
            if not metin:
                raise CevapUretilemedi("LLM boş cevap döndü")
            return metin, toplam_kullanim

        messages.append(msg)
        for tc in tool_calls:
            try:
                argumanlar = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError:
                argumanlar = {}
            metin, _hata = araclar.arac_calistir(tc["function"]["name"], argumanlar)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": metin,
            })

    raise CevapUretilemedi(f"{_max_tur()} turda cevap üretilemedi")
