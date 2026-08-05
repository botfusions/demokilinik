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


def prompt_hazirla(gecmis: list[dict], mesaj: str) -> str:
    satirlar = []
    if gecmis:
        satirlar.append("Bu hastayla önceki yazışman (eskiden yeniye):")
        for g in gecmis:
            kim = "Hasta" if g["yon"] == "gelen" else "Sen"
            satirlar.append(f"{kim}: {g['mesaj']}")
        satirlar.append("")

    satirlar.append(f"Hastanın yeni mesajı: {mesaj}")
    satirlar.append("")
    satirlar.append("Hastaya WhatsApp'tan gönderilecek cevabı yaz. Sadece cevabı yaz.")
    return "\n".join(satirlar)


def cevap_uret(gecmis: list[dict], mesaj: str) -> tuple[str, float | None]:
    """(yanıt, maliyet_usd) döner. Başarısızlıkta CevapUretilemedi."""
    prompt = prompt_hazirla(gecmis, mesaj)

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
