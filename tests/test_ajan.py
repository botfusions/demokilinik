"""Tam ajan — tool-calling döngüsü. Hermes CLI yok, doğrudan `/chat/completions`.

Gerçek LLM çağrılmaz: `httpx.post` monkeypatch edilir, sahte cevap dizileriyle
döngünün doğru mesaj geçmişi biriktirdiği ve tavan aşılınca durduğu doğrulanır.
"""

import pytest

from app import ajan, araclar


def _ortam(monkeypatch):
    monkeypatch.setenv("AJAN_PROVIDER", "zai")
    monkeypatch.setenv("ZAI_API_KEY", "sahte")
    monkeypatch.setenv("AJAN_MODEL", "sahte-model")


def _sahte_post(cevaplar: list[dict], yakalanan: list | None = None):
    """`cevaplar` sırayla dönülür: her biri choices[0].message'a karşılık gelir."""
    sira = iter(cevaplar)

    class SahteYanit:
        def __init__(self, mesaj):
            self._mesaj = mesaj

        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": self._mesaj}], "usage": {}}

    def post(url, **kw):
        if yakalanan is not None:
            yakalanan.append(kw.get("json"))
        return SahteYanit(next(sira))

    return post


def test_tool_call_sonrasi_duz_metinle_biter(monkeypatch):
    _ortam(monkeypatch)
    monkeypatch.setattr(
        araclar, "arac_calistir",
        lambda ad, arg: ('{"doktorlar": []}', False),
    )

    yakalanan = []
    cevaplar = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "1", "function": {"name": "doktorlari_getir", "arguments": "{}"}}
        ]},
        {"role": "assistant", "content": "İşte doktorlarımız.", "tool_calls": None},
    ]
    monkeypatch.setattr(ajan.httpx, "post", _sahte_post(cevaplar, yakalanan))

    yanit, maliyet = ajan.cevap_uret([], "Hangi doktorlar müsait?")

    assert yanit == "İşte doktorlarımız."
    # İkinci istekte tool sonucu mesaj geçmişine eklenmiş olmalı
    ikinci_istek_mesajlari = yakalanan[1]["messages"]
    roller = [m["role"] for m in ikinci_istek_mesajlari]
    assert "tool" in roller


def test_tavan_asilinca_hata_firlatir(monkeypatch):
    """Model sürekli aynı aracı çağırırsa döngü sonsuza gitmemeli."""
    _ortam(monkeypatch)
    monkeypatch.setenv("AJAN_MAX_TUR", "3")
    monkeypatch.setattr(araclar, "arac_calistir", lambda ad, arg: ("{}", False))

    tool_call_mesaji = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "1", "function": {"name": "doktorlari_getir", "arguments": "{}"}}
    ]}
    monkeypatch.setattr(ajan.httpx, "post", _sahte_post([tool_call_mesaji] * 3))

    with pytest.raises(ajan.CevapUretilemedi):
        ajan.cevap_uret([], "Randevu almak istiyorum")


def test_instagram_kanalinda_arac_semasi_bos(monkeypatch):
    """Instagram'da randevu araçları hiç şemaya girmemeli."""
    _ortam(monkeypatch)
    yakalanan = []
    monkeypatch.setattr(
        ajan.httpx, "post",
        _sahte_post([{"content": "Bilgi cevabı.", "tool_calls": None}], yakalanan),
    )

    ajan.cevap_uret([], "Randevu almak istiyorum", kanal="instagram")

    assert yakalanan[0]["tools"] is None


def test_whatsapp_kanalinda_arac_semasi_dolu(monkeypatch):
    _ortam(monkeypatch)
    yakalanan = []
    monkeypatch.setattr(
        ajan.httpx, "post",
        _sahte_post([{"content": "cevap", "tool_calls": None}], yakalanan),
    )

    ajan.cevap_uret([], "Merhaba", kanal="whatsapp")

    assert len(yakalanan[0]["tools"]) == 7


def test_bos_cevap_hata_firlatir(monkeypatch):
    _ortam(monkeypatch)
    monkeypatch.setattr(
        ajan.httpx, "post",
        _sahte_post([{"content": "   ", "tool_calls": None}]),
    )
    with pytest.raises(ajan.CevapUretilemedi):
        ajan.cevap_uret([], "Merhaba")


def test_anahtar_yoksa_hata_firlatir(monkeypatch):
    monkeypatch.setenv("AJAN_PROVIDER", "zai")
    monkeypatch.delenv("ZAI_API_KEY", raising=False)
    with pytest.raises(ajan.CevapUretilemedi):
        ajan.cevap_uret([], "Merhaba")
