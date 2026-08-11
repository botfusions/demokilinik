"""Konum iğnesi: işaret ayıklama, koordinat okuma, iki kanalda da sızmama."""

import pytest

from app.ajan import klinik_konumu, konum_ayikla


# ── işaret ayıklama ─────────────────────────────────────────

def test_isaret_ayiklanir():
    metin, istendi = konum_ayikla("Bağdat Cad. No:120, Kadıköy.\n[KONUM]")
    assert istendi is True
    assert metin == "Bağdat Cad. No:120, Kadıköy."


def test_isaretsiz_cevap_degismez():
    metin, istendi = konum_ayikla("İmplant 25.000 TL.")
    assert istendi is False
    assert metin == "İmplant 25.000 TL."


def test_isaret_metnin_ortasindaysa_da_temizlenir():
    """Model işareti araya koyarsa hastaya ham metin gitmemeli."""
    metin, istendi = konum_ayikla("Adresimiz [KONUM] Bağdat Cad.")
    assert istendi is True
    assert "[KONUM]" not in metin


def test_yalniz_isaretten_ibaret_cevap():
    metin, istendi = konum_ayikla("[KONUM]")
    assert (metin, istendi) == ("", True)


# ── koordinat okuma ─────────────────────────────────────────

def test_gecerli_koordinat(monkeypatch):
    monkeypatch.setenv("KLINIK_KONUM", "40.9812, 29.0578")
    assert klinik_konumu() == (40.9812, 29.0578)


@pytest.mark.parametrize("deger", ["", "  ", "abc", "41.0", "41.0,", "41,29,5",
                                   "95.0,29.0", "41.0,200.0"])
def test_bozuk_koordinat_none_doner(monkeypatch, deger):
    """Bozuk değer patlamamalı: konum atlanır, adres yazıyla gider."""
    monkeypatch.setenv("KLINIK_KONUM", deger)
    assert klinik_konumu() is None


def test_tanimsizsa_none(monkeypatch):
    monkeypatch.delenv("KLINIK_KONUM", raising=False)
    assert klinik_konumu() is None


# ── WhatsApp akışı ──────────────────────────────────────────

@pytest.fixture
def webhook_ortami(conn, monkeypatch):
    """_mesaji_isle'yi LLM ve ağ olmadan koşturur; gönderilenleri toplar."""
    import app.main as main

    gonderilen = {"metin": [], "konum": []}
    monkeypatch.setattr(main.openwa, "mesaj_gonder",
                        lambda tel, m: gonderilen["metin"].append((tel, m)) or "wa1")
    monkeypatch.setattr(main.openwa, "konum_gonder",
                        lambda tel, e, b: gonderilen["konum"].append((tel, e, b)) or "wa2")
    return main, gonderilen


def _ajan_cevabi(monkeypatch, yanit):
    import app.main as main

    monkeypatch.setattr(
        main.ajan, "cevap_uret",
        lambda g, m, kanal="whatsapp", **kw: (yanit, {"prompt_tokens": 100, "completion_tokens": 50}),
    )


def test_isaretli_cevapta_konum_gider(webhook_ortami, monkeypatch, conn):
    main, gonderilen = webhook_ortami
    monkeypatch.setenv("KLINIK_KONUM", "40.9812,29.0578")
    _ajan_cevabi(monkeypatch, "Bağdat Cad. No:120.\n[KONUM]")

    main._mesaji_isle("905321112233", "adresiniz neresi", "wa-in-1", "Ali")

    assert gonderilen["konum"] == [("905321112233", 40.9812, 29.0578)]
    assert "[KONUM]" not in gonderilen["metin"][0][1]


def test_isaretsiz_cevapta_konum_gitmez(webhook_ortami, monkeypatch, conn):
    main, gonderilen = webhook_ortami
    monkeypatch.setenv("KLINIK_KONUM", "40.9812,29.0578")
    _ajan_cevabi(monkeypatch, "İmplant 25.000 TL.")

    main._mesaji_isle("905321112233", "implant kaç para", "wa-in-2", None)

    assert gonderilen["konum"] == []


def test_koordinat_yoksa_metin_yine_gider(webhook_ortami, monkeypatch, conn):
    """Konum tanımsızken adres cevabı kaybolmamalı."""
    main, gonderilen = webhook_ortami
    monkeypatch.delenv("KLINIK_KONUM", raising=False)
    _ajan_cevabi(monkeypatch, "Bağdat Cad. No:120.\n[KONUM]")

    main._mesaji_isle("905321112233", "adres", "wa-in-3", None)

    assert gonderilen["konum"] == []
    assert gonderilen["metin"][0][1] == "Bağdat Cad. No:120."


def test_konum_gonderimi_patlarsa_mesaj_yine_kaydedilir(webhook_ortami, monkeypatch, conn):
    main, gonderilen = webhook_ortami
    monkeypatch.setenv("KLINIK_KONUM", "40.9812,29.0578")
    monkeypatch.setattr(main.openwa, "konum_gonder",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("ağ yok")))
    _ajan_cevabi(monkeypatch, "Bağdat Cad. No:120.\n[KONUM]")

    main._mesaji_isle("905321112233", "adres", "wa-in-4", None)

    from app.crm import gorusme_gecmisi, kisi_bul
    kisi = kisi_bul(conn, "905321112233")
    assert [g["yon"] for g in gorusme_gecmisi(conn, kisi["id"])] == ["gelen", "giden"]


def test_isaret_veritabanina_yazilmaz(webhook_ortami, monkeypatch, conn):
    """Personel panelde ham işaret görmemeli."""
    main, _ = webhook_ortami
    monkeypatch.setenv("KLINIK_KONUM", "40.9812,29.0578")
    _ajan_cevabi(monkeypatch, "Bağdat Cad. No:120.\n[KONUM]")

    main._mesaji_isle("905321112233", "adres", "wa-in-5", None)

    from app.crm import gorusme_gecmisi, kisi_bul
    kisi = kisi_bul(conn, "905321112233")
    assert all("[KONUM]" not in g["mesaj"] for g in gorusme_gecmisi(conn, kisi["id"]))


# ── Instagram: iğne yok ama işaret yine temizlenmeli ────────

def test_instagramda_isaret_sizmaz(conn, monkeypatch):
    from app import instagram

    monkeypatch.setattr(
        "app.ajan.cevap_uret",
        lambda g, m, kanal="whatsapp", **kw: (
            "Bağdat Cad. No:120.\n[KONUM]", {"prompt_tokens": 100, "completion_tokens": 50},
        ),
    )
    monkeypatch.setattr(instagram, "okundu_isaretle", lambda _: None)
    monkeypatch.setattr(instagram, "yeni_mesajlar", lambda: [
        {"igsid": "17841400000000001", "mesaj_id": "ig:k1", "metin": "adres", "ad": None}])

    gonderilen = []
    instagram.tur_calistir(conn, gonder_fn=lambda ig, m: gonderilen.append(m) or "x")

    assert gonderilen == ["Bağdat Cad. No:120."]
