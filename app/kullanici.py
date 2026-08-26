"""Kullanıcılar, paroları ve işlem izi.

Panelde iki rol var: **admin** kullanıcı açıp parola tanımlar, **personel** günlük
işi yapar. Her değiştirici işlem `islem_kaydi`'na kimin yaptığıyla birlikte düşer —
"randevuyu kim iptal etti" sorusunun cevabı olmadan bu panel bir kliniğe verilemez.

Parola hash'i stdlib `hashlib.scrypt` ile; harici bcrypt/argon2 bağımlılığı yok.
"""

import hashlib
import hmac
import os
import secrets

import psycopg
from psycopg.rows import dict_row

# scrypt parametreleri. n=2^16 → 128*n*r ≈ 67MB iş belleği: sözlük saldırısı için
# pahalı, doğrulama ~60ms sürer (panel girişinde görünmez). OpenSSL'in varsayılan
# 32MB maxmem sınırı bunu reddettiği için sınır açıkça yükseltiliyor.
_N, _R, _P = 2 ** 16, 8, 1
_MAXMEM = 192 * 1024 * 1024


class ParolaZayif(Exception):
    """Parola asgari uzunluğu karşılamıyor."""


class KullaniciVar(Exception):
    """Bu kullanıcı adı zaten alınmış."""


class HesapKilitli(Exception):
    """Art arda hatalı deneme sonrası hesap süreli kilitlendi."""


# Giriş kilidi. Süreli — kalıcı kilit, paneli internete açıkken bilinen bir
# kullanıcı adını kilitleyip kliniği dışarıda bırakmanın yolunu verir.
MAX_DENEME = 5


def _kilit_dakika() -> int:
    return int(os.environ.get("KULLANICI_KILIT_DAKIKA", "15"))


def parola_hash(parola: str) -> str:
    if len(parola) < 8:
        raise ParolaZayif("Parola en az 8 karakter olmalı")

    tuz = secrets.token_bytes(16)
    ozet = hashlib.scrypt(parola.encode(), salt=tuz, n=_N, r=_R, p=_P, dklen=32, maxmem=_MAXMEM)
    return f"scrypt${_N}${_R}${_P}${tuz.hex()}${ozet.hex()}"


def parola_dogrula(parola: str, kayitli: str) -> bool:
    try:
        yontem, n, r, p, tuz_hex, ozet_hex = kayitli.split("$")
        if yontem != "scrypt":
            return False
        ozet = hashlib.scrypt(
            parola.encode(), salt=bytes.fromhex(tuz_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(bytes.fromhex(ozet_hex)),
            maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False

    return hmac.compare_digest(ozet.hex(), ozet_hex)


# ── kullanıcılar ────────────────────────────────────────────

def kullanici_ekle(conn: psycopg.Connection, kullanici_adi: str, parola: str,
                   rol: str = "personel", ad: str | None = None,
                   telefon: str | None = None) -> int:
    kullanici_adi = kullanici_adi.strip().lower()
    ozet = parola_hash(parola)   # zayıf parola burada patlar, kayıt açılmaz

    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM kullanicilar WHERE kullanici_adi = %s", (kullanici_adi,))
        if cur.fetchone():
            raise KullaniciVar(f"'{kullanici_adi}' zaten kayıtlı")

        cur.execute(
            """
            INSERT INTO kullanicilar (kullanici_adi, ad, parola_hash, rol, telefon)
            VALUES (%s, %s, %s, %s, %s) RETURNING id
            """,
            (kullanici_adi, ad, ozet, rol, (telefon or "").strip() or None),
        )
        kid = cur.fetchone()[0]
    conn.commit()
    return kid


def kullanici_dogrula(conn: psycopg.Connection, kullanici_adi: str,
                      parola: str) -> dict | None:
    """Doğru kullanıcı+parola ise kullanıcıyı döner, değilse None.

    Pasifleştirilmiş kullanıcı doğru parolayla da giremez. Art arda
    `MAX_DENEME` hatalı denemeden sonra hesap `_kilit_dakika()` dakika
    kilitlenir; kilitliyken DOĞRU parola da `HesapKilitli` fırlatır —
    kilidin sessiz "yanlış parola" gibi görünmesi kalan süreyi saklamaz,
    personel ne olduğunu bilsin.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT *, now() AS simdiki FROM kullanicilar WHERE kullanici_adi = %s",
            (kullanici_adi.strip().lower(),),
        )
        k = cur.fetchone()

    if not k:
        return None

    if k["kilit_bitis"] and k["kilit_bitis"] > k["simdiki"]:
        raise HesapKilitli(str(k["kilit_bitis"]))

    if not k["aktif"] or not parola_dogrula(parola, k["parola_hash"]):
        with conn.cursor() as cur:
            if k["basarisiz_deneme"] + 1 >= MAX_DENEME:
                cur.execute(
                    "UPDATE kullanicilar SET basarisiz_deneme = 0, kilit_bitis = now()"
                    " + make_interval(mins => %s) WHERE id = %s",
                    (_kilit_dakika(), k["id"]),
                )
            else:
                cur.execute(
                    "UPDATE kullanicilar SET basarisiz_deneme = %s WHERE id = %s",
                    (k["basarisiz_deneme"] + 1, k["id"]),
                )
        conn.commit()
        return None

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE kullanicilar SET son_giris = now(), basarisiz_deneme = 0,"
            " kilit_bitis = NULL WHERE id = %s",
            (k["id"],),
        )
    conn.commit()
    return k


def kullanicilar_listele(conn: psycopg.Connection) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, kullanici_adi, ad, rol, aktif, son_giris, olusturma, telefon
              FROM kullanicilar ORDER BY aktif DESC, rol, kullanici_adi
            """
        )
        return cur.fetchall()


def kullanici_telefon_yaz(conn: psycopg.Connection, kullanici_id: int,
                          telefon: str | None) -> None:
    """Bildirim WhatsApp'ı — boş gelirse silinir, bildirim o kişiye gitmez."""
    with conn.cursor() as cur:
        cur.execute("UPDATE kullanicilar SET telefon = %s WHERE id = %s",
                    ((telefon or "").strip() or None, kullanici_id))
    conn.commit()


def bildirim_numaralari(conn: psycopg.Connection) -> list[str]:
    """Devir bildirimi gidecek numaralar: telefonlu aktif kullanıcılar."""
    with conn.cursor() as cur:
        cur.execute("SELECT telefon FROM kullanicilar WHERE aktif AND telefon IS NOT NULL")
        return [r[0] for r in cur.fetchall()]


def kullanici_getir(conn: psycopg.Connection, kullanici_id: int) -> dict | None:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, kullanici_adi, ad, rol, aktif FROM kullanicilar WHERE id = %s",
            (kullanici_id,),
        )
        return cur.fetchone()


def parola_degistir(conn: psycopg.Connection, kullanici_id: int, yeni: str) -> None:
    ozet = parola_hash(yeni)
    with conn.cursor() as cur:
        cur.execute("UPDATE kullanicilar SET parola_hash = %s WHERE id = %s", (ozet, kullanici_id))
    conn.commit()


def kullanici_durum_yaz(conn: psycopg.Connection, kullanici_id: int, aktif: bool) -> None:
    """Son aktif admini pasifleştirmeye izin verilmez — panel kilitlenir."""
    with conn.cursor() as cur:
        if not aktif:
            cur.execute(
                "SELECT count(*) FROM kullanicilar WHERE rol = 'admin' AND aktif AND id <> %s",
                (kullanici_id,),
            )
            if cur.fetchone()[0] == 0:
                cur.execute("SELECT rol FROM kullanicilar WHERE id = %s", (kullanici_id,))
                satir = cur.fetchone()
                if satir and satir[0] == "admin":
                    raise ValueError("Son yönetici pasifleştirilemez — panele kimse giremez")

        cur.execute("UPDATE kullanicilar SET aktif = %s WHERE id = %s", (aktif, kullanici_id))
    conn.commit()


def ilk_admin_kur(conn: psycopg.Connection) -> str | None:
    """Hiç kullanıcı yoksa .env'deki PANEL_PAROLA ile 'admin' açar.

    Kurulumun kesintisiz olması için: kullanıcı sistemi eklenmeden önce kurulmuş
    bir panel, güncellemeden sonra aynı parolayla açılmaya devam eder.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM kullanicilar")
        if cur.fetchone()[0]:
            return None

    parola = os.environ.get("PANEL_PAROLA")
    if not parola or len(parola) < 8:
        return None

    kullanici_ekle(conn, "admin", parola, rol="admin", ad="Yönetici")
    return "admin"


# ── işlem izi ───────────────────────────────────────────────

def islem_yaz(conn: psycopg.Connection, kullanici: dict | None,
              eylem: str, detay: str | None = None) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO islem_kaydi (kullanici_id, kullanici_adi, eylem, detay)
            VALUES (%s, %s, %s, %s)
            """,
            (
                (kullanici or {}).get("id"),
                (kullanici or {}).get("kullanici_adi", "?"),
                eylem,
                detay,
            ),
        )
    conn.commit()


def islem_kayitlari(conn: psycopg.Connection, limit: int = 100) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT * FROM islem_kaydi ORDER BY olusturma DESC, id DESC LIMIT %s", (limit,)
        )
        return cur.fetchall()
