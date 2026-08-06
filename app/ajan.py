"""Hermes köprüsü — `hermes -z` ile tek atış cevap üretimi.

Oturum yönetimi yok: konuşma geçmişinin tek kaynağı Postgres, her çağrıda
prompt'a konur. İki yerde state tutmak senkron sorunu demek.
"""

import json
import os
import subprocess
import tempfile
from pathlib import Path

PROJE_KOKU = Path(__file__).resolve().parent.parent
ZAMAN_ASIMI = int(os.environ.get("AJAN_ZAMAN_ASIMI", "120"))


class CevapUretilemedi(Exception):
    """Hermes cevap üretemedi — zaman aşımı, çökme ya da boş çıktı."""


def _whatsapp_hatti() -> str:
    return os.environ.get("KLINIK_WHATSAPP_NUMARASI", "")


def prompt_hazirla(gecmis: list[dict], mesaj: str, kanal: str = "whatsapp") -> str:
    satirlar = []
    if gecmis:
        satirlar.append("Bu hastayla önceki yazışman (eskiden yeniye):")
        for g in gecmis:
            kim = "Hasta" if g["yon"] == "gelen" else "Sen"
            satirlar.append(f"{kim}: {g['mesaj']}")
        satirlar.append("")

    satirlar.append(f"Hastanın yeni mesajı: {mesaj}")
    satirlar.append("")

    if kanal == "instagram":
        # Instagram bilgilendirme kanalı: randevu açma yetkisi YOK. Bu sınır
        # burada duruyor çünkü tek fark kanal; ajanın kimliği ve bilgi tabanı aynı.
        hat = _whatsapp_hatti()
        nereye = f"WhatsApp hattımıza ({hat})" if hat else "WhatsApp hattımıza"
        satirlar.append(
            "Bu mesaj Instagram'dan geldi. Instagram yalnızca bilgilendirme kanalıdır:\n"
            "- Soruyu bilgi tabanındaki bilgilerle cevapla (hizmet, fiyat, çalışma saatleri, adres).\n"
            f"- Randevu almak, değiştirmek veya iptal etmek isteyen kişiyi {nereye} yönlendir.\n"
            "- Randevu KAYDI AÇMA, saat verme, 'ayırdım/kaydettim' deme. Bu kanalda o yetkin yok.\n"
            "- Randevu araçlarını çağırma."
        )
        satirlar.append("")
        satirlar.append("Instagram'dan gönderilecek cevabı yaz. Sadece cevabı yaz.")
    else:
        satirlar.append("Hastaya WhatsApp'tan gönderilecek cevabı yaz. Sadece cevabı yaz.")

    return "\n".join(satirlar)


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
               kanal: str = "whatsapp") -> tuple[str, float | None]:
    """(yanıt, maliyet_usd) döner. Başarısızlıkta CevapUretilemedi."""
    prompt = prompt_hazirla(gecmis, mesaj, kanal)

    ortam = os.environ.copy()
    # Ajan bu klasöre özel — global ~/.hermes asla kullanılmaz
    ortam["HERMES_HOME"] = str(PROJE_KOKU / "hermes-home")

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tf:
        rapor = Path(tf.name)

    # Test ortamında ZAI/GLM, VPS'te OpenAI — .env'de iki satır değişir,
    # config.yaml'a dokunmadan. Boş bırakılırsa config.yaml'daki model geçerli.
    komut = ["hermes", "-z", prompt, "--usage-file", str(rapor)]
    if os.environ.get("AJAN_PROVIDER"):
        komut += ["--provider", os.environ["AJAN_PROVIDER"]]
    if os.environ.get("AJAN_MODEL"):
        komut += ["--model", os.environ["AJAN_MODEL"]]

    try:
        sonuc = subprocess.run(
            komut,
            cwd=PROJE_KOKU,          # .hermes.md buradan okunur
            env=ortam,
            capture_output=True,
            text=True,
            timeout=ZAMAN_ASIMI,
        )
    except subprocess.TimeoutExpired as e:
        raise CevapUretilemedi(f"hermes {ZAMAN_ASIMI}s içinde cevap vermedi") from e
    except FileNotFoundError as e:
        raise CevapUretilemedi("hermes komutu bulunamadı — kurulum yapıldı mı?") from e
    finally:
        maliyet = None
        if rapor.exists():
            try:
                maliyet = json.loads(rapor.read_text()).get("estimated_cost_usd")
            except (json.JSONDecodeError, OSError):
                pass
            rapor.unlink(missing_ok=True)

    if sonuc.returncode != 0:
        raise CevapUretilemedi(f"hermes exit {sonuc.returncode}: {sonuc.stderr[:500]}")

    yanit = sonuc.stdout.strip()
    if not yanit:
        raise CevapUretilemedi("hermes boş cevap döndü")

    return yanit, maliyet
