"""`python -m egitim` — LLM'siz parse-yolu self-check (ponytail)."""

from egitim import _json_ayistir, _temizle

ornek = ('{"kayitlar": [{"baslik": "Çalışma saatleri", "icerik": "Cumartesi 10-16 açık", '
         '"kategori": "calisma_saatleri"}], "uyarilar": ["fiyat: implant 25.000 TL"]}')
k, u = _temizle(_json_ayistir(ornek))
assert len(k) == 1 and k[0]["kategori"] == "calisma_saatleri", k
assert u and "fiyat" in u[0], u
# geçersiz kategori -> genel
k2, _ = _temizle(_json_ayistir(
    '{"kayitlar": [{"baslik": "X", "icerik": "Y", "kategori": "bilinmiyor"}]}'))
assert k2[0]["kategori"] == "genel", k2
print("egitim: parse yolu tamam")
