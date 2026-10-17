import customtkinter as ctk
import tkinter as tk
from tkinter import messagebox
import sqlite3
import os
import hashlib
import time
import threading
import smtplib
import re
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# --- DONANIM KÜTÜPHANESİ ---
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False

# --- TEMA VE RENK PALETİ ---
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

RENK_ARKAPLAN   = "#0f172a"
RENK_PANEL      = "#1e293b"
RENK_KART       = "#1e293b"
RENK_LISTE_BG   = "#0f172a"
RENK_MAVI       = "#2563eb"
RENK_MAVI_HOVER = "#1d4ed8"
RENK_YESIL      = "#16a34a"
RENK_YESIL_HVR  = "#15803d"
RENK_KIRMIZI    = "#dc2626"
RENK_KIRMIZI_HV = "#b91c1c"
RENK_MOR        = "#7c3aed"
RENK_MOR_HOVER  = "#6d28d9"
RENK_SARI       = "#f59e0b"
RENK_METIN_GRİ  = "#94a3b8"

# --- DOSYA YOLLARI ---
DB_ISMI   = "klinik.db"
DOSYA_LOG = "sistem_log.txt"

app_state = {
    "aktif_kullanici": "",
    "aktif_rol": "",
    "aktif_tarih": datetime.now().strftime("%Y-%m-%d"),
    "yanlis_deneme": 0,
    "kilitlenme_bitis": 0.0,
}

# Listbox indekslerini doğrudan veritabanı kayıtlarıyla eşleştiren güvenli listeler
gosterilen_randevular = []  # [(tarih, saat, hasta_isim, eposta), ...]
gosterilen_yedekler   = []  # [(sira, tarih, hasta_isim, eposta, durum), ...]

# --- YARDIMCI FONKSİYONLAR ---
def ayar_oku(key, default):
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                satir = line.strip()
                if satir and not satir.startswith("#") and satir.startswith(key):
                    parcalar = satir.split("=", 1)
                    if len(parcalar) == 2:
                        return parcalar[1].strip().strip('"').strip("'")
    return default

EPOSTA_ADRESI     = ayar_oku("EPOSTA_ADRESI", "")
EPOSTA_SIFRESI    = ayar_oku("EPOSTA_UYGULAMA_SIFRESI", "")
SENSOR_PORT_AYARI = ayar_oku("SENSOR_PORT", "COM3")

def sifre_hashle(sifre: str) -> str:
    return hashlib.sha256(sifre.encode("utf-8")).hexdigest()

def log_yaz(islem_detayi: str):
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    kullanici = app_state["aktif_kullanici"] if app_state["aktif_kullanici"] else "SISTEM"
    try:
        with open(DOSYA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{zaman}] [Kullanici: {kullanici}] -> {islem_detayi}\n")
    except Exception as e:
        print(f"Log yazılamadı: {e}")

def eposta_gecerli_mi(eposta: str) -> bool:
    if eposta == "":
        return True
    regex = r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$"
    return re.match(regex, eposta) is not None

def saat_formatla(saat_ham: str):
    if ":" not in saat_ham:
        raise ValueError()
    saat_kismi, dakika_kismi = saat_ham.split(":", 1)
    s, d = int(saat_kismi), int(dakika_kismi)
    if not (0 <= s <= 23 and 0 <= d <= 59):
        raise ValueError()
    return f"{s:02d}:{d:02d}"

# --- ARKA PLAN E-POSTA GÖNDERİMİ ---
def eposta_thread(alici_eposta, hasta_isim, tarih, saat, otonom_mu=False):
    if not alici_eposta:
        return

    def gonder():
        if not EPOSTA_ADRESI or not EPOSTA_SIFRESI:
            log_yaz("KRİTİK HATA: .env dosyasında e-posta bilgileri eksik!")
            return

        mesaj = MIMEMultipart()
        mesaj["From"] = EPOSTA_ADRESI
        mesaj["To"] = alici_eposta
        mesaj["Subject"] = "SmartClinic - Randevu Onayı"

        ek_bilgi = "\n(Yedek listedeki sıranız gelmiş ve boşalan seansa otomatik atamanız yapılmıştır.)\n" if otonom_mu else "\n"
        govde = (
            f"Sayın {hasta_isim},\n"
            f"{ek_bilgi}\n"
            f"SmartClinic randevunuz aşağıdaki detaylarla oluşturulmuştur:\n"
            f"📅 Tarih : {tarih}\n"
            f"⏰ Saat  : {saat}\n\n"
            f"Sağlıklı günler dileriz.\n"
            f"SmartClinic Otomasyon Sistemi"
        )
        mesaj.attach(MIMEText(govde, "plain", "utf-8"))

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587, timeout=15)
            server.starttls()
            server.login(EPOSTA_ADRESI, EPOSTA_SIFRESI)
            server.sendmail(EPOSTA_ADRESI, alici_eposta, mesaj.as_string())
            server.quit()
            log_yaz(f"E-posta başarıyla gönderildi: {alici_eposta} ({tarih} {saat})")
        except Exception as e:
            log_yaz(f"KRİTİK HATA: E-posta gönderilemedi! Detay: {e}")

    threading.Thread(target=gonder, daemon=True).start()

# --- SQLITE VERİ TABANI YÖNETİMİ ---
def db_kurulum():
    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (
                    kullanici_adi TEXT PRIMARY KEY,
                    sifre_hash TEXT,
                    rol TEXT
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS randevular (
                    tarih TEXT,
                    saat TEXT,
                    hasta_isim TEXT,
                    eposta TEXT,
                    PRIMARY KEY (tarih, saat)
                )''')
    c.execute('''CREATE TABLE IF NOT EXISTS yedekler (
                    sira INTEGER PRIMARY KEY AUTOINCREMENT,
                    tarih TEXT,
                    hasta_isim TEXT,
                    eposta TEXT,
                    durum TEXT
                )''')

    c.execute("SELECT 1 FROM kullanicilar WHERE kullanici_adi='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO kullanicilar VALUES (?, ?, ?)", ("admin", sifre_hashle("1234"), "admin"))

    conn.commit()
    conn.close()

# --- ARAYÜZ VE İŞ MANTIĞI FONKSİYONLARI ---
def arayuzu_guncelle():
    global gosterilen_randevular, gosterilen_yedekler

    aranan = entry_arama.get().strip()
    if aranan != "":
        randevu_ara()
        return

    listbox_randevular.delete(0, tk.END)
    listbox_yedek.delete(0, tk.END)
    gosterilen_randevular.clear()
    gosterilen_yedekler.clear()

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    tarih = app_state["aktif_tarih"]

    # 1. O güne ait randevuları çek
    c.execute("SELECT tarih, saat, hasta_isim, eposta FROM randevular WHERE tarih=? ORDER BY saat ASC", (tarih,))
    randevular = c.fetchall()
    for r in randevular:
        gosterilen_randevular.append(r)
        mail_ikon = "✉️" if r[3] else "  "
        listbox_randevular.insert(tk.END, f"  ⏰ {r[1]}   │   {r[2]:<28} {mail_ikon}")

    # 2. O güne ait yedekleri çek (Önce ACİL olanlar, sonra kayıt sırasına göre)
    c.execute("""
        SELECT sira, tarih, hasta_isim, eposta, durum
        FROM yedekler
        WHERE tarih=?
        ORDER BY CASE WHEN durum='Acil' THEN 0 ELSE 1 END, sira ASC
    """, (tarih,))
    yedekler = c.fetchall()

    acil_sayisi = 0
    for idx, y in enumerate(yedekler, start=1):
        gosterilen_yedekler.append(y)
        if y[4] == "Acil":
            acil_sayisi += 1
            etiket = "[ACİL - ÖNCELİKLİ]"
        else:
            etiket = "[Normal Sıra]"
        mail_ikon = "✉️" if y[3] else ""
        listbox_yedek.insert(tk.END, f"  #{idx:02d}  │  {etiket:<19} │  {y[2]} {mail_ikon}")

    conn.close()

    # İstatistik Kartlarını Güncelle
    lbl_stat_randevu_deger.configure(text=str(len(randevular)))
    lbl_stat_yedek_deger.configure(text=str(len(yedekler)))
    lbl_stat_acil_deger.configure(text=str(acil_sayisi))
    lbl_aktif_tarih.configure(text=tarih)
    lbl_liste_baslik.configure(text=f"📅 GÜNLÜK RANDEVU PLANI ({tarih})")

def tarih_degistir(delta_gun: int):
    mevcut = datetime.strptime(app_state["aktif_tarih"], "%Y-%m-%d")
    yeni = mevcut + timedelta(days=delta_gun)
    app_state["aktif_tarih"] = yeni.strftime("%Y-%m-%d")
    entry_arama.delete(0, tk.END)
    log_yaz(f"Aktif tarih değiştirildi: {app_state['aktif_tarih']}")
    arayuzu_guncelle()

def bugune_don():
    bugun = datetime.now().strftime("%Y-%m-%d")
    if app_state["aktif_tarih"] != bugun:
        app_state["aktif_tarih"] = bugun
        entry_arama.delete(0, tk.END)
        arayuzu_guncelle()

def randevu_olustur():
    saat_ham   = entry_saat.get().strip()
    isim       = entry_randevu_isim.get().strip()
    hasta_mail = entry_eposta.get().strip()
    tarih      = app_state["aktif_tarih"]

    if saat_ham == "" or isim == "":
        messagebox.showwarning("Eksik Bilgi", "Saat ve Hasta Ad Soyad alanları boş bırakılamaz!")
        return

    if hasta_mail and not eposta_gecerli_mi(hasta_mail):
        messagebox.showwarning("Format Hatası", "Lütfen geçerli bir e-posta adresi girin!")
        return

    try:
        saat = saat_formatla(saat_ham)
    except ValueError:
        messagebox.showwarning("Saat Hatası", "Lütfen geçerli bir saat girin! (Örn: 09:00 veya 14:30)")
        return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()

    try:
        c.execute("INSERT INTO randevular (tarih, saat, hasta_isim, eposta) VALUES (?, ?, ?, ?)",
                  (tarih, saat, isim, hasta_mail))
        conn.commit()
        log_yaz(f"YENİ RANDEVU eklendi: {tarih} {saat} -> {isim}")
        if hasta_mail:
            eposta_thread(hasta_mail, isim, tarih, saat, otonom_mu=False)

        entry_saat.delete(0, tk.END)
        entry_randevu_isim.delete(0, tk.END)
        entry_eposta.delete(0, tk.END)
    except sqlite3.IntegrityError:
        messagebox.showwarning("Seans Dolu", f"{tarih} tarihinde {saat} seansı zaten dolu!\nLütfen farklı bir saat seçin veya mevcut randevuyu iptal edin.")
    finally:
        conn.close()
        arayuzu_guncelle()

def yedek_hasta_ekle():
    hasta_adi  = entry_yeni_hasta.get().strip()
    hasta_mail = entry_yedek_eposta.get().strip()
    durum      = aciliyet_secimi.get()
    tarih      = app_state["aktif_tarih"]

    if hasta_adi == "":
        messagebox.showwarning("Eksik Bilgi", "Lütfen yedek kuyruğa eklenecek hastanın adını girin!")
        return

    if hasta_mail and not eposta_gecerli_mi(hasta_mail):
        messagebox.showwarning("Format Hatası", "Lütfen geçerli bir e-posta adresi girin!")
        return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("INSERT INTO yedekler (tarih, hasta_isim, eposta, durum) VALUES (?, ?, ?, ?)",
              (tarih, hasta_adi, hasta_mail, durum))
    conn.commit()
    conn.close()

    log_yaz(f"Yedek hasta eklendi: {tarih} -> {hasta_adi} ({durum})")

    entry_yeni_hasta.delete(0, tk.END)
    entry_yedek_eposta.delete(0, tk.END)
    aciliyet_secimi.set("Normal")
    arayuzu_guncelle()

def randevu_iptal_et():
    secili = listbox_randevular.curselection()
    if not secili:
        messagebox.showwarning("Seçim Yapılmadı", "Lütfen listeden iptal edilecek randevuyu seçin!")
        return

    indeks = secili[0]
    if indeks >= len(gosterilen_randevular):
        return

    tarih, saat, hasta_isim, _ = gosterilen_randevular[indeks]

    cevap = messagebox.askyesno(
        "Randevu İptal Onayı",
        f"📅 Tarih: {tarih}\n⏰ Saat: {saat}\n👤 Hasta: {hasta_isim}\n\n"
        f"Bu randevu iptal edilecek ve sırada bekleyen öncelikli yedek hasta varsa otomatik atanacak. Emin misiniz?"
    )
    if not cevap:
        return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()

    c.execute("DELETE FROM randevular WHERE tarih=? AND saat=?", (tarih, saat))
    log_yaz(f"Randevu iptal edildi: {tarih} {saat} ({hasta_isim})")

    # Otonom Atama: Önce ACİL hastalar, ardından sırası önde olanlar
    c.execute("""
        SELECT sira, hasta_isim, eposta, durum
        FROM yedekler
        WHERE tarih=?
        ORDER BY CASE WHEN durum='Acil' THEN 0 ELSE 1 END, sira ASC
        LIMIT 1
    """, (tarih,))
    yedek = c.fetchone()

    if yedek:
        sira, y_isim, y_eposta, y_durum = yedek
        c.execute("DELETE FROM yedekler WHERE sira=?", (sira,))
        c.execute("INSERT INTO randevular (tarih, saat, hasta_isim, eposta) VALUES (?, ?, ?, ?)",
                  (tarih, saat, y_isim, y_eposta))
        log_yaz(f"OTONOM ATAMA: {tarih} {saat} seansına '{y_isim}' ({y_durum}) yerleştirildi.")

        if y_eposta:
            eposta_thread(y_eposta, y_isim, tarih, saat, otonom_mu=True)
            messagebox.showinfo(
                "Otonom Atama Başarılı",
                f"Randevu iptal edildi.\n\n"
                f"Sırada bekleyen [{y_isim} - {y_durum}] otomatik olarak {saat} seansına atandı ve bilgilendirme e-postası gönderildi."
            )
        else:
            messagebox.showinfo(
                "Otonom Atama Başarılı",
                f"Randevu iptal edildi.\n\n"
                f"Sırada bekleyen [{y_isim} - {y_durum}] otomatik olarak {saat} seansına atandı.\n(E-posta kayıtlı olmadığı için bildirim gönderilmedi.)"
            )
    else:
        messagebox.showinfo("Bilgi", f"{tarih} günü için bekleyen yedek hasta bulunmadığından {saat} seansı boş bırakıldı.")

    conn.commit()
    conn.close()
    arayuzu_guncelle()

def randevu_ara(event=None):
    global gosterilen_randevular
    aranan = entry_arama.get().strip().lower()

    if aranan == "":
        arayuzu_guncelle()
        return

    listbox_randevular.delete(0, tk.END)
    gosterilen_randevular.clear()

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("""
        SELECT tarih, saat, hasta_isim, eposta
        FROM randevular
        WHERE LOWER(hasta_isim) LIKE ? OR saat LIKE ? OR tarih LIKE ?
        ORDER BY tarih ASC, saat ASC
    """, (f"%{aranan}%", f"%{aranan}%", f"%{aranan}%"))

    sonuclar = c.fetchall()
    for r in sonuclar:
        gosterilen_randevular.append(r)
        mail_ikon = "✉️" if r[3] else ""
        listbox_randevular.insert(tk.END, f"  📅 [{r[0]}]  ⏰ {r[1]}  │  {r[2]} {mail_ikon}")
    conn.close()

    lbl_liste_baslik.configure(text=f"🔍 ARAMA SONUÇLARI ({len(sonuclar)} Kayıt Bulundu)")

def yedek_sil():
    secili = listbox_yedek.curselection()
    if not secili:
        messagebox.showwarning("Seçim Yapılmadı", "Lütfen silmek istediğiniz yedek hastayı seçin!")
        return

    indeks = secili[0]
    if indeks >= len(gosterilen_yedekler):
        return

    sira_id, tarih, isim, _, durum = gosterilen_yedekler[indeks]

    if messagebox.askyesno("Kuyruktan Çıkar", f"Kuyruktaki '{isim}' ({durum}) kaydı silinecek. Emin misiniz?"):
        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        c.execute("DELETE FROM yedekler WHERE sira=?", (sira_id,))
        conn.commit()
        conn.close()
        log_yaz(f"Yedek kuyruktan silindi: {tarih} -> {isim} (Sıra ID: {sira_id})")
        arayuzu_guncelle()

def randevu_duzenle(event=None):
    secili = listbox_randevular.curselection()
    if not secili:
        return

    indeks = secili[0]
    if indeks >= len(gosterilen_randevular):
        return

    eski_tarih, eski_saat, eski_isim, eski_eposta = gosterilen_randevular[indeks]

    pencere_duzenle = ctk.CTkToplevel(pencere)
    pencere_duzenle.title("Randevu Düzenle")
    pencere_duzenle.geometry("380x360")
    pencere_duzenle.resizable(False, False)
    pencere_duzenle.grab_set()

    ctk.CTkLabel(pencere_duzenle, text="Randevu Bilgilerini Güncelle", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(20, 5))
    ctk.CTkLabel(pencere_duzenle, text=f"Seçili Kayıt: {eski_tarih} | {eski_saat}", font=ctk.CTkFont(size=12), text_color=RENK_METIN_GRİ).pack(pady=(0, 15))

    ctk.CTkLabel(pencere_duzenle, text="Saat (SS:DD):", anchor="w").pack(fill="x", padx=40)
    yeni_saat_entry = ctk.CTkEntry(pencere_duzenle, height=35)
    yeni_saat_entry.insert(0, eski_saat)
    yeni_saat_entry.pack(fill="x", padx=40, pady=(2, 10))

    ctk.CTkLabel(pencere_duzenle, text="Hasta Ad Soyad:", anchor="w").pack(fill="x", padx=40)
    yeni_isim_entry = ctk.CTkEntry(pencere_duzenle, height=35)
    yeni_isim_entry.insert(0, eski_isim)
    yeni_isim_entry.pack(fill="x", padx=40, pady=(2, 10))

    ctk.CTkLabel(pencere_duzenle, text="E-posta Adresi:", anchor="w").pack(fill="x", padx=40)
    yeni_eposta_entry = ctk.CTkEntry(pencere_duzenle, height=35)
    yeni_eposta_entry.insert(0, eski_eposta if eski_eposta else "")
    yeni_eposta_entry.pack(fill="x", padx=40, pady=(2, 15))

    def kaydet_duzenle():
        y_saat_ham = yeni_saat_entry.get().strip()
        y_isim     = yeni_isim_entry.get().strip()
        y_eposta   = yeni_eposta_entry.get().strip()

        if not y_saat_ham or not y_isim:
            messagebox.showwarning("Uyarı", "Saat ve Hasta Adı boş bırakılamaz!", parent=pencere_duzenle)
            return

        if y_eposta and not eposta_gecerli_mi(y_eposta):
            messagebox.showwarning("Uyarı", "Geçersiz e-posta formatı!", parent=pencere_duzenle)
            return

        try:
            y_saat = saat_formatla(y_saat_ham)
        except ValueError:
            messagebox.showwarning("Hata", "Geçerli bir saat girin! (Örn: 14:30)", parent=pencere_duzenle)
            return

        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        try:
            c.execute("""
                UPDATE randevular
                SET saat=?, hasta_isim=?, eposta=?
                WHERE tarih=? AND saat=?
            """, (y_saat, y_isim, y_eposta, eski_tarih, eski_saat))
            conn.commit()
            log_yaz(f"Randevu düzenlendi ({eski_tarih}): {eski_saat} '{eski_isim}' -> {y_saat} '{y_isim}'")
            arayuzu_guncelle()
            pencere_duzenle.destroy()
        except sqlite3.IntegrityError:
            messagebox.showwarning("Çakışma Hatası", f"{eski_tarih} tarihinde {y_saat} seansı başka bir hastaya ait!", parent=pencere_duzenle)
        finally:
            conn.close()

    ctk.CTkButton(pencere_duzenle, text="DEĞİŞİKLİKLERİ KAYDET", command=kaydet_duzenle, fg_color=RENK_MAVI, hover_color=RENK_MAVI_HOVER, height=38, font=ctk.CTkFont(weight="bold")).pack(fill="x", padx=40, pady=5)

def gunu_kapat_ve_rapor_al():
    tarih = app_state["aktif_tarih"]
    cevap = messagebox.askyesno(
        "Günü Kapat ve Rapor Al",
        f"Aktif gün ({tarih}) için özet rapor dosyası oluşturulacak, o günün kayıtları temizlenip bir sonraki güne geçilecek.\n\nOnaylıyor musunuz?"
    )
    if not cevap:
        return

    zaman_etiketi = datetime.now().strftime("%Y%m%d_%H%M%S")
    rapor_adi = f"Gunluk_Rapor_{tarih}_{zaman_etiketi}.txt"

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("SELECT saat, hasta_isim, eposta FROM randevular WHERE tarih=? ORDER BY saat ASC", (tarih,))
    randevular = c.fetchall()
    c.execute("""
        SELECT hasta_isim, durum, eposta
        FROM yedekler
        WHERE tarih=?
        ORDER BY CASE WHEN durum='Acil' THEN 0 ELSE 1 END, sira ASC
    """, (tarih,))
    yedekler = c.fetchall()

    try:
        with open(rapor_adi, "w", encoding="utf-8") as f:
            f.write("====================================================\n")
            f.write("          SMARTCLINIC GÜNLÜK ÖZET RAPORU            \n")
            f.write("====================================================\n")
            f.write(f"Oluşturulma Tarihi : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Raporu Alan Yetkili: {app_state['aktif_kullanici']} ({app_state['aktif_rol']})\n")
            f.write(f"Kapatılan Gün      : {tarih}\n")
            f.write(f"Toplam Randevu     : {len(randevular)}\n")
            f.write(f"Bekleyen Yedek     : {len(yedekler)}\n")
            f.write("----------------------------------------------------\n\n")

            f.write("=== TAMAMLANAN / DOLU RANDEVULAR ===\n")
            if randevular:
                for r in randevular:
                    mail_bilgi = f" ({r[2]})" if r[2] else ""
                    f.write(f"  • {r[0]} -> {r[1]}{mail_bilgi}\n")
            else:
                f.write("  (Kayıt Yok)\n")

            f.write("\n=== DEVREDİLMEYEN YEDEK KUYRUĞU ===\n")
            if yedekler:
                for idx, y in enumerate(yedekler, start=1):
                    mail_bilgi = f" - {y[2]}" if y[2] else ""
                    f.write(f"  {idx:02d}. [{y[1]}] {y[0]}{mail_bilgi}\n")
            else:
                f.write("  (Kayıt Yok)\n")
    except Exception as e:
        messagebox.showerror("Dosya Hatası", f"Rapor dosyası yazılamadı:\n{e}")
        conn.close()
        return

    c.execute("DELETE FROM randevular WHERE tarih=?", (tarih,))
    c.execute("DELETE FROM yedekler WHERE tarih=?", (tarih,))
    conn.commit()
    conn.close()

    tarih_degistir(1)
    log_yaz(f"Gün sonu raporu alındı ve sıfırlandı: {rapor_adi}")
    messagebox.showinfo("Rapor Hazır", f"Gün başarıyla kapatıldı ve rapor kaydedildi:\n{rapor_adi}")

# --- LOG GÖRÜNTÜLEYİCİ PENCERESİ (ADMİN ÖZEL) ---
def log_penceresi_ac():
    pencere_log = ctk.CTkToplevel(pencere)
    pencere_log.title("Sistem Güvenlik ve İşlem Logları")
    pencere_log.geometry("760x500")
    pencere_log.grab_set()

    ust_bar = ctk.CTkFrame(pencere_log, fg_color="transparent")
    ust_bar.pack(fill="x", padx=20, pady=(15, 5))

    ctk.CTkLabel(ust_bar, text="📜 Sistem İşlem Kayıtları (sistem_log.txt)", font=ctk.CTkFont(size=16, weight="bold")).pack(side="left")

    txt_log = ctk.CTkTextbox(pencere_log, font=("Consolas", 12), wrap="none")
    txt_log.pack(fill="both", expand=True, padx=20, pady=10)

    def loglari_yukle():
        txt_log.configure(state="normal")
        txt_log.delete("1.0", tk.END)
        if os.path.exists(DOSYA_LOG):
            with open(DOSYA_LOG, "r", encoding="utf-8") as f:
                icerik = f.read()
                txt_log.insert("1.0", icerik if icerik else "Henüz log kaydı bulunmuyor.")
                txt_log.see(tk.END)
        else:
            txt_log.insert("1.0", "Log dosyası henüz oluşturulmamış.")
        txt_log.configure(state="disabled")

    ctk.CTkButton(ust_bar, text="🔄 Yenile", width=90, height=30, command=loglari_yukle).pack(side="right")
    loglari_yukle()

# --- GİRİŞ, ÇIKIŞ VE KULLANICI YÖNETİMİ ---
def giris_yap():
    su_an = time.time()
    if app_state["yanlis_deneme"] >= 5:
        kalan = int(app_state["kilitlenme_bitis"] - su_an)
        if kalan > 0:
            messagebox.showerror("Hesap Kilitlendi", f"Çok fazla başarısız giriş denemesi!\nLütfen {kalan} saniye bekleyin.")
            return
        else:
            app_state["yanlis_deneme"] = 0

    kul = entry_kullanici.get().strip()
    sif = entry_sifre.get().strip()

    if not kul or not sif:
        messagebox.showwarning("Uyarı", "Kullanıcı adı ve şifre alanlarını doldurun.")
        return

    hashli_sifre = sifre_hashle(sif)

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("SELECT rol FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=?", (kul, hashli_sifre))
    kullanici = c.fetchone()
    conn.close()

    if kullanici:
        app_state["aktif_kullanici"] = kul
        app_state["aktif_rol"]       = kullanici[0]
        app_state["yanlis_deneme"]   = 0
        log_yaz("Sisteme başarılı giriş yapıldı.")

        entry_sifre.delete(0, tk.END)
        frame_giris.pack_forget()
        frame_ana_uygulama.pack(fill="both", expand=True)

        rol_etiketi = "YÖNETİCİ / ADMIN" if app_state["aktif_rol"] == "admin" else "KLİNİK PERSONELİ"
        lbl_karsilama.configure(text=f"👤 {kul.upper()}")
        lbl_rol_rozet.configure(text=rol_etiketi)

        if app_state["aktif_rol"] == "admin":
            admin_panel_frame.pack(fill="x", padx=18, pady=(0, 8), after=ust_kullanici_frame)
        else:
            admin_panel_frame.pack_forget()

        arayuzu_guncelle()
    else:
        app_state["yanlis_deneme"] += 1
        app_state["kilitlenme_bitis"] = su_an + 30
        kalan_hak = 5 - app_state["yanlis_deneme"]
        log_yaz(f"Başarısız giriş denemesi! Kullanıcı: {kul} | Kalan hak: {kalan_hak}")
        if kalan_hak > 0:
            messagebox.showerror("Erişim Reddedildi", f"Kullanıcı adı veya şifre hatalı!\nKalan deneme hakkı: {kalan_hak}")
        else:
            messagebox.showerror("Hesap Kilitlendi", "5 hatalı deneme yapıldı! Giriş 30 saniye süreyle kilitlendi.")

def oturumu_kapat():
    if messagebox.askyesno("Çıkış Yap", "Mevcut oturumu kapatıp giriş ekranına dönmek istiyor musunuz?"):
        log_yaz("Kullanıcı oturumu kapattı.")
        app_state["aktif_kullanici"] = ""
        app_state["aktif_rol"] = ""
        frame_ana_uygulama.pack_forget()
        frame_giris.pack(expand=True)

def yeni_kayit_penceresi():
    pencere_kayit = ctk.CTkToplevel(pencere)
    pencere_kayit.title("Yeni Personel / Yönetici Ekle")
    pencere_kayit.geometry("360x400")
    pencere_kayit.resizable(False, False)
    pencere_kayit.grab_set()

    ctk.CTkLabel(pencere_kayit, text="Yeni Hesap Oluştur", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(25, 15))

    yeni_kul = ctk.CTkEntry(pencere_kayit, placeholder_text="Kullanıcı Adı", width=240, height=38)
    yeni_kul.pack(pady=8)
    yeni_sifre = ctk.CTkEntry(pencere_kayit, placeholder_text="Şifre", show="*", width=240, height=38)
    yeni_sifre.pack(pady=8)

    ctk.CTkLabel(pencere_kayit, text="Yetki Grubu:", font=ctk.CTkFont(size=12, weight="bold")).pack(pady=(10, 4))
    rol_secimi = ctk.StringVar(value="personel")
    rol_frame = ctk.CTkFrame(pencere_kayit, fg_color="transparent")
    rol_frame.pack(pady=4)
    ctk.CTkRadioButton(rol_frame, text="Personel", variable=rol_secimi, value="personel").pack(side="left", padx=10)
    ctk.CTkRadioButton(rol_frame, text="Admin", variable=rol_secimi, value="admin").pack(side="left", padx=10)

    def kaydi_tamamla():
        k = yeni_kul.get().strip()
        s = yeni_sifre.get().strip()
        r = rol_secimi.get()
        if k == "" or s == "":
            messagebox.showwarning("Hata", "Kullanıcı adı ve şifre boş bırakılamaz!", parent=pencere_kayit)
            return

        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO kullanicilar (kullanici_adi, sifre_hash, rol) VALUES (?, ?, ?)",
                      (k, sifre_hashle(s), r))
            conn.commit()
            log_yaz(f"Sisteme yeni kullanıcı eklendi: {k} (Rol: {r})")
            messagebox.showinfo("Başarılı", f"'{k}' hesabı başarıyla oluşturuldu.", parent=pencere_kayit)
            pencere_kayit.destroy()
        except sqlite3.IntegrityError:
            messagebox.showwarning("Hata", "Bu kullanıcı adı zaten kayıtlı!", parent=pencere_kayit)
        finally:
            conn.close()

    ctk.CTkButton(pencere_kayit, text="HESABI OLUŞTUR", command=kaydi_tamamla, fg_color=RENK_YESIL, hover_color=RENK_YESIL_HVR, width=240, height=40, font=ctk.CTkFont(weight="bold")).pack(pady=25)

def uygulamayi_kapat():
    if app_state["aktif_kullanici"]:
        log_yaz("Uygulama kapatıldı.")
    if sensor_port and sensor_port.is_open:
        try:
            sensor_port.close()
        except Exception:
            pass
    pencere.destroy()

# --- BAŞLANGIÇ TEMİZLİK VE VERİTABANI KURULUMU ---
try:
    for silinecek in ["randevu_verileri.json", "kullanicilar.json", "randevu_verileri_YEDEK.json"]:
        if os.path.exists(silinecek):
            os.remove(silinecek)
except Exception as e:
    print(f"Eski dosya temizlik hatası: {e}")

db_kurulum()

# ==============================================================================
#                              ARAYÜZ KURULUMU (UI)
# ==============================================================================
pencere = ctk.CTk()
pencere.title("SmartClinic Pro v9.0 - Akıllı Klinik & Otonom Randevu Sistemi")
pencere.geometry("1120x820")
pencere.minsize(1000, 750)
pencere.configure(fg_color=RENK_ARKAPLAN)
pencere.protocol("WM_DELETE_WINDOW", uygulamayi_kapat)

# ------------------------- 1. GİRİŞ EKRANI -------------------------
frame_giris = ctk.CTkFrame(pencere, width=420, height=480, corner_radius=20, fg_color=RENK_PANEL, border_width=1, border_color="#334155")
frame_giris.pack(expand=True)
frame_giris.pack_propagate(False)

ctk.CTkLabel(frame_giris, text="🏥 SMARTCLINIC PRO", font=ctk.CTkFont(size=26, weight="bold"), text_color="#f8fafc").pack(pady=(45, 4))
ctk.CTkLabel(frame_giris, text="Akıllı Klinik Yönetim ve Otomasyon Sistemi", font=ctk.CTkFont(size=12), text_color=RENK_METIN_GRİ).pack(pady=(0, 30))

entry_kullanici = ctk.CTkEntry(frame_giris, placeholder_text="👤 Kullanıcı Adı", width=290, height=44, corner_radius=10)
entry_kullanici.pack(pady=8)

entry_sifre = ctk.CTkEntry(frame_giris, placeholder_text="🔒 Şifre", show="*", width=290, height=44, corner_radius=10)
entry_sifre.pack(pady=8)
entry_sifre.bind("<Return>", lambda e: giris_yap())

def sifre_gorunum_degistir():
    entry_sifre.configure(show="" if chk_sifre_goster.get() == 1 else "*")

chk_sifre_goster = ctk.CTkCheckBox(frame_giris, text="Şifreyi Göster", command=sifre_gorunum_degistir, font=ctk.CTkFont(size=12), checkbox_width=18, checkbox_height=18)
chk_sifre_goster.pack(pady=(4, 15), padx=65, anchor="w")

btn_giris = ctk.CTkButton(frame_giris, text="SİSTEME GİRİŞ YAP", command=giris_yap, width=290, height=46, corner_radius=10, fg_color=RENK_MAVI, hover_color=RENK_MAVI_HOVER, font=ctk.CTkFont(size=14, weight="bold"))
btn_giris.pack(pady=10)

ctk.CTkLabel(frame_giris, text="SQLite & SHA-256 Güvenlik Mimarisi", font=ctk.CTkFont(size=11), text_color="#64748b").pack(side="bottom", pady=18)

# ------------------------- 2. ANA UYGULAMA EKRANI -------------------------
frame_ana_uygulama = ctk.CTkFrame(pencere, fg_color="transparent")

# === SOL KONTROL PANELİ ===
sol_panel = ctk.CTkFrame(frame_ana_uygulama, width=330, corner_radius=0, fg_color=RENK_PANEL)
sol_panel.pack(side="left", fill="y")
sol_panel.pack_propagate(False)

# Kullanıcı Üst Kartı
ust_kullanici_frame = ctk.CTkFrame(sol_panel, fg_color="#0f172a", corner_radius=10)
ust_kullanici_frame.pack(fill="x", padx=18, pady=(15, 8))

kullanici_bilgi_sol = ctk.CTkFrame(ust_kullanici_frame, fg_color="transparent")
kullanici_bilgi_sol.pack(side="left", padx=12, pady=8)

lbl_karsilama = ctk.CTkLabel(kullanici_bilgi_sol, text="👤 KULLANICI", font=ctk.CTkFont(size=13, weight="bold"), text_color="#f8fafc", anchor="w")
lbl_karsilama.pack(anchor="w")
lbl_rol_rozet = ctk.CTkLabel(kullanici_bilgi_sol, text="ROL", font=ctk.CTkFont(size=10, weight="bold"), text_color=RENK_SARI, anchor="w")
lbl_rol_rozet.pack(anchor="w")

btn_cikis = ctk.CTkButton(ust_kullanici_frame, text="Çıkış", command=oturumu_kapat, width=60, height=28, fg_color="#334155", hover_color=RENK_KIRMIZI, font=ctk.CTkFont(size=11, weight="bold"))
btn_cikis.pack(side="right", padx=10)

# Admin Özel Butonlar (Personel Ekle & Loglar)
admin_panel_frame = ctk.CTkFrame(sol_panel, fg_color="transparent")
btn_kayit_panel = ctk.CTkButton(admin_panel_frame, text="+ Personel Ekle", command=yeni_kayit_penceresi, width=142, height=30, fg_color="#334155", hover_color="#475569", font=ctk.CTkFont(size=12))
btn_kayit_panel.pack(side="left", padx=(0, 5), expand=True, fill="x")
btn_log_panel = ctk.CTkButton(admin_panel_frame, text="📜 Log Kayıtları", command=log_penceresi_ac, width=142, height=30, fg_color="#334155", hover_color="#475569", font=ctk.CTkFont(size=12))
btn_log_panel.pack(side="right", padx=(5, 0), expand=True, fill="x")

# Tarih Navigasyon Barı
tarih_frame = ctk.CTkFrame(sol_panel, fg_color="#0f172a", corner_radius=10)
tarih_frame.pack(fill="x", padx=18, pady=(4, 10))

ctk.CTkButton(tarih_frame, text="◀", width=34, height=30, fg_color="#334155", hover_color="#475569", command=lambda: tarih_degistir(-1)).pack(side="left", padx=6, pady=6)
lbl_aktif_tarih = ctk.CTkLabel(tarih_frame, text=app_state["aktif_tarih"], font=ctk.CTkFont(size=13, weight="bold"))
lbl_aktif_tarih.pack(side="left", expand=True)
ctk.CTkButton(tarih_frame, text="Bugün", width=52, height=26, fg_color="#1e3a8a", hover_color=RENK_MAVI, font=ctk.CTkFont(size=11), command=bugune_don).pack(side="left", padx=2)
ctk.CTkButton(tarih_frame, text="▶", width=34, height=30, fg_color="#334155", hover_color="#475569", command=lambda: tarih_degistir(1)).pack(side="right", padx=6, pady=6)

# Yeni Randevu Form Kartı
randevu_form_kart = ctk.CTkFrame(sol_panel, fg_color="#0f172a", corner_radius=10)
randevu_form_kart.pack(fill="x", padx=18, pady=6)

ctk.CTkLabel(randevu_form_kart, text="➕ YENİ RANDEVU OLUŞTUR", font=ctk.CTkFont(size=12, weight="bold"), text_color="#60a5fa").pack(anchor="w", padx=12, pady=(10, 4))
entry_saat = ctk.CTkEntry(randevu_form_kart, placeholder_text="Saat (Örn: 09:30 veya 14:15)", height=32)
entry_saat.pack(pady=4, padx=12, fill="x")
entry_randevu_isim = ctk.CTkEntry(randevu_form_kart, placeholder_text="Hasta Ad Soyad", height=32)
entry_randevu_isim.pack(pady=4, padx=12, fill="x")
entry_eposta = ctk.CTkEntry(randevu_form_kart, placeholder_text="Hasta E-posta (İsteğe Bağlı)", height=32)
entry_eposta.pack(pady=4, padx=12, fill="x")

btn_yeni_randevu = ctk.CTkButton(randevu_form_kart, text="RANDEVU EKLE", command=randevu_olustur, fg_color=RENK_MAVI, hover_color=RENK_MAVI_HOVER, height=34, font=ctk.CTkFont(weight="bold"))
btn_yeni_randevu.pack(pady=(6, 12), padx=12, fill="x")

# Yedek Kuyruk Form Kartı
yedek_form_kart = ctk.CTkFrame(sol_panel, fg_color="#0f172a", corner_radius=10)
yedek_form_kart.pack(fill="x", padx=18, pady=6)

ctk.CTkLabel(yedek_form_kart, text="⏳ YEDEK KUYRUĞA EKLE", font=ctk.CTkFont(size=12, weight="bold"), text_color="#4ade80").pack(anchor="w", padx=12, pady=(10, 4))
entry_yeni_hasta = ctk.CTkEntry(yedek_form_kart, placeholder_text="Hasta Ad Soyad", height=32)
entry_yeni_hasta.pack(pady=4, padx=12, fill="x")
entry_yedek_eposta = ctk.CTkEntry(yedek_form_kart, placeholder_text="E-posta (Otonom Bildirim İçin)", height=32)
entry_yedek_eposta.pack(pady=4, padx=12, fill="x")

radio_frame = ctk.CTkFrame(yedek_form_kart, fg_color="transparent")
radio_frame.pack(fill="x", padx=12, pady=4)
aciliyet_secimi = ctk.StringVar(value="Normal")
ctk.CTkRadioButton(radio_frame, text="Normal", variable=aciliyet_secimi, value="Normal", radiobutton_width=18, radiobutton_height=18).pack(side="left", padx=(2, 15))
ctk.CTkRadioButton(radio_frame, text="Acil (Öncelikli)", variable=aciliyet_secimi, value="Acil", text_color="#f87171", radiobutton_width=18, radiobutton_height=18).pack(side="left")

btn_ekle = ctk.CTkButton(yedek_form_kart, text="KUYRUĞA EKLE", command=yedek_hasta_ekle, fg_color=RENK_YESIL, hover_color=RENK_YESIL_HVR, height=34, font=ctk.CTkFont(weight="bold"))
btn_ekle.pack(pady=(6, 12), padx=12, fill="x")

# Alt Aksiyon Butonları
btn_iptal = ctk.CTkButton(sol_panel, text="🗑️ SEÇİLİ RANDEVUYU İPTAL ET\n(Otonom Yedek Atama)", command=randevu_iptal_et, fg_color=RENK_KIRMIZI, hover_color=RENK_KIRMIZI_HV, height=46, font=ctk.CTkFont(size=12, weight="bold"))
btn_iptal.pack(pady=(8, 4), padx=18, fill="x")

btn_rapor = ctk.CTkButton(sol_panel, text="📊 GÜNÜ KAPAT & RAPOR AL", command=gunu_kapat_ve_rapor_al, fg_color=RENK_MOR, hover_color=RENK_MOR_HOVER, height=38, font=ctk.CTkFont(weight="bold"))
btn_rapor.pack(side="bottom", pady=15, padx=18, fill="x")

# === SAĞ DASHBOARD PANELİ ===
sag_panel = ctk.CTkFrame(frame_ana_uygulama, fg_color="transparent")
sag_panel.pack(side="right", fill="both", expand=True, padx=25, pady=18)

# 1. Sensör Durum Çubuğu
sensor_frame = ctk.CTkFrame(sag_panel, fg_color=RENK_KART, corner_radius=12, border_width=1, border_color="#334155")
sensor_frame.pack(fill="x", pady=(0, 12))

ctk.CTkLabel(sensor_frame, text="🌡️ AKILLI ATEŞ ÖLÇER SENSÖRÜ", font=ctk.CTkFont(size=13, weight="bold"), text_color="#e2e8f0").pack(side="left", padx=16, pady=10)

sensor_sag_kisim = ctk.CTkFrame(sensor_frame, fg_color="transparent")
sensor_sag_kisim.pack(side="right", padx=12)

lbl_sensor = ctk.CTkLabel(sensor_sag_kisim, text="Başlatılıyor...", font=ctk.CTkFont(size=14, weight="bold"), text_color=RENK_SARI)
lbl_sensor.pack(side="left", padx=10)

sensor_port = None
def sensor_baglan():
    global sensor_port
    if not SERIAL_AVAILABLE:
        lbl_sensor.configure(text="pyserial Kurulu Değil", text_color=RENK_METIN_GRİ)
        return
    try:
        if sensor_port and sensor_port.is_open:
            sensor_port.close()
        sensor_port = serial.Serial(SENSOR_PORT_AYARI, 9600, timeout=0.1)
        lbl_sensor.configure(text=f"Bağlandı ({SENSOR_PORT_AYARI}) - Ölçüm Bekleniyor...", text_color="#4ade80")
    except Exception:
        sensor_port = None
        lbl_sensor.configure(text=f"Port Yok ({SENSOR_PORT_AYARI})", text_color="#f87171")

btn_sensor_yenile = ctk.CTkButton(sensor_sag_kisim, text="🔄 Bağlan", width=75, height=26, fg_color="#334155", hover_color="#475569", font=ctk.CTkFont(size=11), command=sensor_baglan)
btn_sensor_yenile.pack(side="right")

# 2. Üçlü İstatistik (KPI) Kartları
kpi_frame = ctk.CTkFrame(sag_panel, fg_color="transparent")
kpi_frame.pack(fill="x", pady=(0, 14))
kpi_frame.grid_columnconfigure((0, 1, 2), weight=1)

kart_1 = ctk.CTkFrame(kpi_frame, fg_color=RENK_KART, corner_radius=12, border_width=1, border_color="#334155")
kart_1.grid(row=0, column=0, sticky="ew", padx=(0, 6))
ctk.CTkLabel(kart_1, text="DOLU SEANS", font=ctk.CTkFont(size=11, weight="bold"), text_color=RENK_METIN_GRİ).pack(pady=(10, 0))
lbl_stat_randevu_deger = ctk.CTkLabel(kart_1, text="0", font=ctk.CTkFont(size=24, weight="bold"), text_color="#60a5fa")
lbl_stat_randevu_deger.pack(pady=(0, 10))

kart_2 = ctk.CTkFrame(kpi_frame, fg_color=RENK_KART, corner_radius=12, border_width=1, border_color="#334155")
kart_2.grid(row=0, column=1, sticky="ew", padx=6)
ctk.CTkLabel(kart_2, text="TOPLAM YEDEK KUYRUK", font=ctk.CTkFont(size=11, weight="bold"), text_color=RENK_METIN_GRİ).pack(pady=(10, 0))
lbl_stat_yedek_deger = ctk.CTkLabel(kart_2, text="0", font=ctk.CTkFont(size=24, weight="bold"), text_color="#fbbf24")
lbl_stat_yedek_deger.pack(pady=(0, 10))

kart_3 = ctk.CTkFrame(kpi_frame, fg_color=RENK_KART, corner_radius=12, border_width=1, border_color="#334155")
kart_3.grid(row=0, column=2, sticky="ew", padx=(6, 0))
ctk.CTkLabel(kart_3, text="ACİL BEKLEYEN", font=ctk.CTkFont(size=11, weight="bold"), text_color=RENK_METIN_GRİ).pack(pady=(10, 0))
lbl_stat_acil_deger = ctk.CTkLabel(kart_3, text="0", font=ctk.CTkFont(size=24, weight="bold"), text_color="#f87171")
lbl_stat_acil_deger.pack(pady=(0, 10))

# 3. Arama Çubuğu
arama_frame = ctk.CTkFrame(sag_panel, fg_color=RENK_KART, corner_radius=10, border_width=1, border_color="#334155")
arama_frame.pack(fill="x", pady=(0, 12))

ctk.CTkLabel(arama_frame, text="🔍", font=ctk.CTkFont(size=15)).pack(side="left", padx=(12, 4), pady=6)
entry_arama = ctk.CTkEntry(arama_frame, placeholder_text="Tüm tarihlerde hasta adı, saat veya tarih (YYYY-AA-GG) ara...", border_width=0, fg_color="transparent", height=32)
entry_arama.pack(side="left", fill="x", expand=True, padx=4, pady=4)
entry_arama.bind("<KeyRelease>", randevu_ara)

def aramayi_temizle():
    entry_arama.delete(0, tk.END)
    arayuzu_guncelle()

ctk.CTkButton(arama_frame, text="Temizle", width=65, height=26, fg_color="#334155", hover_color="#475569", font=ctk.CTkFont(size=11), command=aramayi_temizle).pack(side="right", padx=8)

# 4. Randevu Listesi Bölümü
randevu_baslik_frame = ctk.CTkFrame(sag_panel, fg_color="transparent")
randevu_baslik_frame.pack(fill="x", pady=(0, 4))
lbl_liste_baslik = ctk.CTkLabel(randevu_baslik_frame, text="📅 GÜNLÜK RANDEVU PLANI", font=ctk.CTkFont(size=15, weight="bold"))
lbl_liste_baslik.pack(side="left")
ctk.CTkLabel(randevu_baslik_frame, text="💡 Düzenlemek için randevuya çift tıklayın", font=ctk.CTkFont(size=11), text_color=RENK_METIN_GRİ).pack(side="right")

randevu_liste_cerceve = ctk.CTkFrame(sag_panel, fg_color=RENK_LISTE_BG, corner_radius=10, border_width=1, border_color="#334155")
randevu_liste_cerceve.pack(fill="both", expand=True, pady=(0, 12))

listbox_randevular = tk.Listbox(
    randevu_liste_cerceve, height=8, bg=RENK_LISTE_BG, fg="#f1f5f9",
    font=("Consolas", 13), borderwidth=0, highlightthickness=0,
    selectbackground=RENK_MAVI, selectforeground="#ffffff",
    activestyle="none", exportselection=False
)
scroll_randevu = ctk.CTkScrollbar(randevu_liste_cerceve, command=listbox_randevular.yview)
listbox_randevular.configure(yscrollcommand=scroll_randevu.set)

listbox_randevular.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
scroll_randevu.pack(side="right", fill="y", padx=4, pady=8)
listbox_randevular.bind("<Double-Button-1>", randevu_duzenle)

# 5. Bekleyen Yedek Listesi Bölümü
yedek_baslik_frame = ctk.CTkFrame(sag_panel, fg_color="transparent")
yedek_baslik_frame.pack(fill="x", pady=(2, 4))
ctk.CTkLabel(yedek_baslik_frame, text="⏳ BEKLEYEN YEDEK KUYRUĞU (Acil Öncelikli)", font=ctk.CTkFont(size=15, weight="bold")).pack(side="left")

btn_yedek_sil = ctk.CTkButton(
    yedek_baslik_frame, text="✕ Seçili Yedeği Sil", command=yedek_sil,
    width=130, height=26, fg_color="transparent", hover_color="#450a0a",
    border_width=1, border_color="#ef4444", text_color="#f87171", font=ctk.CTkFont(size=12)
)
btn_yedek_sil.pack(side="right")

yedek_liste_cerceve = ctk.CTkFrame(sag_panel, fg_color=RENK_LISTE_BG, corner_radius=10, border_width=1, border_color="#334155")
yedek_liste_cerceve.pack(fill="both", expand=True)

listbox_yedek = tk.Listbox(
    yedek_liste_cerceve, height=6, bg=RENK_LISTE_BG, fg="#f1f5f9",
    font=("Consolas", 13), borderwidth=0, highlightthickness=0,
    selectbackground=RENK_YESIL, selectforeground="#ffffff",
    activestyle="none", exportselection=False
)
scroll_yedek = ctk.CTkScrollbar(yedek_liste_cerceve, command=listbox_yedek.yview)
listbox_yedek.configure(yscrollcommand=scroll_yedek.set)

listbox_yedek.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
scroll_yedek.pack(side="right", fill="y", padx=4, pady=8)

# --- SENSÖR DİNLEME DÖNGÜSÜ ---
sensor_baglan()

def sensoru_dinle():
    if sensor_port and sensor_port.is_open:
        try:
            if sensor_port.in_waiting > 0:
                veri = sensor_port.readline().decode("utf-8", errors="ignore").strip()
                if veri.startswith("ATES:"):
                    derece = float(veri.split(":")[1])
                    if derece <= 37.5:
                        lbl_sensor.configure(text=f"{derece:.1f} °C (Normal)", text_color="#4ade80")
                    else:
                        lbl_sensor.configure(text=f"⚠️ {derece:.1f} °C (YÜKSEK ATEŞ!)", text_color="#f87171")
        except Exception:
            pass
    pencere.after(1000, sensoru_dinle)

sensoru_dinle()

pencere.mainloop()
