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

# TEMA AYARLARI 
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("blue")

# DOSYA YOLLARI 
DB_ISMI   = "klinik.db"
DOSYA_LOG = "sistem_log.txt"

app_state = {
    "aktif_kullanici": "",
    "aktif_rol": "",
    "aktif_tarih": datetime.now().strftime("%Y-%m-%d"),
    "yanlis_deneme": 0,
    "kilitlenme_bitis": 0.0,
}

# --- YARDIMCI FONKSİYONLAR ---
def ayar_oku(key, default):
    if os.path.exists(".env"):
        with open(".env", "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith(key):
                    return line.split("=", 1)[1].strip()
    return default

EPOSTA_ADRESI = ayar_oku("EPOSTA_ADRESI", "")
EPOSTA_SIFRESI = ayar_oku("EPOSTA_UYGULAMA_SIFRESI", "")
SENSOR_PORT_AYARI = ayar_oku("SENSOR_PORT", "COM3")

def sifre_hashle(sifre: str) -> str:
    return hashlib.sha256(sifre.encode("utf-8")).hexdigest()

def log_yaz(islem_detayi: str):
    zaman = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        with open(DOSYA_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{zaman}] [Kullanici: {app_state['aktif_kullanici']}] -> {islem_detayi}\n")
    except Exception as e:
        print(f"Log yazılamadı: {e}")

def eposta_gecerli_mi(eposta: str) -> bool:
    if eposta == "": return True # E-posta zorunlu değilse
    regex = r'^[a-z0-9]+[\._]?[a-z0-9]+[@]\w+[.]\w+$'
    return re.search(regex, eposta) is not None

# --- ARKA PLAN E-POSTA GÖNDERİMİ ---
def eposta_thread(alici_eposta, hasta_isim, saat):
    if not alici_eposta: return
    aktif_kul = app_state["aktif_kullanici"] 
    def gonder():
        if not EPOSTA_ADRESI or not EPOSTA_SIFRESI:
            log_yaz("KRİTİK HATA: .env dosyasinda e-posta bilgileri eksik!")
            return

        mesaj = MIMEMultipart()
        mesaj["From"] = EPOSTA_ADRESI
        mesaj["To"] = alici_eposta
        mesaj["Subject"] = "SmartClinic Randevu Onayı"

        govde = f"Sayın {hasta_isim},\n\nSmartClinic randevunuz {saat} seansı için başarıyla oluşturulmuştur.\nSağlıklı günler dileriz."
        mesaj.attach(MIMEText(govde, "plain", "utf-8"))

        try:
            server = smtplib.SMTP("smtp.gmail.com", 587)
            server.starttls()
            server.login(EPOSTA_ADRESI, EPOSTA_SIFRESI)
            server.sendmail(EPOSTA_ADRESI, alici_eposta, mesaj.as_string())
            server.quit()
            log_yaz(f"E-posta başariyla gönderildi: {alici_eposta}")
        except Exception as e:
            log_yaz(f"KRİTİK HATA: E-posta gönderilemedi! Detay: {e}")
            
    threading.Thread(target=gonder, daemon=True).start()

# --- SQLITE VERİ TABANI YÖNETİMİ ---
def db_kurulum():
    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    # Şemalar düzeltildi: Tarih ve Rol eklendi
    c.execute('''CREATE TABLE IF NOT EXISTS kullanicilar (kullanici_adi TEXT PRIMARY KEY, sifre_hash TEXT, rol TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS randevular (tarih TEXT, saat TEXT, hasta_isim TEXT, eposta TEXT, PRIMARY KEY (tarih, saat))''')
    c.execute('''CREATE TABLE IF NOT EXISTS yedekler (sira INTEGER PRIMARY KEY AUTOINCREMENT, tarih TEXT, hasta_isim TEXT, eposta TEXT, durum TEXT)''')
    
    # Varsayılan Admin Kontrolü
    c.execute("SELECT * FROM kullanicilar WHERE kullanici_adi='admin'")
    if not c.fetchone():
        c.execute("INSERT INTO kullanicilar VALUES (?, ?, ?)", ("admin", sifre_hashle("1234"), "admin"))
    
    conn.commit()
    conn.close()

# --- ARAYÜZ VE İŞ TARAFINDAKİ SQL İŞLEMLERİ ---
def arayuzu_guncelle():
    listbox_randevular.delete(0, tk.END)
    listbox_yedek.delete(0, tk.END)
    
    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    
    tarih = app_state["aktif_tarih"]
    
    # 1. O güne ait randevuları çek
    c.execute("SELECT saat, hasta_isim FROM randevular WHERE tarih=? ORDER BY saat", (tarih,))
    randevular = c.fetchall()
    for r in randevular:
        listbox_randevular.insert(tk.END, f" {r[0]}  -  {r[1]}")

    # 2. O güne ait yedekleri çek
    c.execute("SELECT hasta_isim, durum FROM yedekler WHERE tarih=? ORDER BY sira", (tarih,))
    yedekler = c.fetchall()
    for y in yedekler:
        listbox_yedek.insert(tk.END, f" {y[0]} ({y[1]})")

    lbl_stat_randevu.configure(text=f"Dolu Seans: {len(randevular)}")
    lbl_stat_yedek.configure(text=f"Kuyruk: {len(yedekler)}")
    lbl_aktif_tarih.configure(text=f"Aktif Tarih: {tarih}")
    
    conn.close()

def tarih_degistir(delta_gun: int):
    mevcut = datetime.strptime(app_state["aktif_tarih"], "%Y-%m-%d")
    yeni   = mevcut + timedelta(days=delta_gun)
    app_state["aktif_tarih"] = yeni.strftime("%Y-%m-%d")
    log_yaz(f"Aktif tarih değiştirildi: {app_state['aktif_tarih']}")
    arayuzu_guncelle()

def randevu_olustur():
    saat_ham = entry_saat.get().strip()
    isim     = entry_randevu_isim.get().strip()
    hasta_mail = entry_eposta.get().strip()
    tarih = app_state["aktif_tarih"]

    if saat_ham == "" or isim == "":
        messagebox.showwarning("Uyarı", "Saat ve İsim alanları boş bırakılamaz")
        return

    if hasta_mail and not eposta_gecerli_mi(hasta_mail):
        messagebox.showwarning("Uyarı", "Geçersiz e-posta formatı!")
        return

    try:
        if ":" not in saat_ham: raise ValueError()
        saat_kismi, dakika_kismi = saat_ham.split(":")
        s, d = int(saat_kismi), int(dakika_kismi)
        if not (0 <= s <= 23 and 0 <= d <= 59): raise ValueError()
        saat = f"{s:02d}:{d:02d}"
    except ValueError:
        messagebox.showwarning("Hata", "Lütfen geçerli bir saat girin! (Örn: 09:00 veya 14:30)")
        return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    
    try:
        c.execute("INSERT INTO randevular (tarih, saat, hasta_isim, eposta) VALUES (?, ?, ?, ?)", (tarih, saat, isim, hasta_mail))
        conn.commit()
        log_yaz(f"YENİ RANDEVU eklendi: {tarih} {saat} -> {isim}")
        if hasta_mail:
            eposta_thread(hasta_mail, isim, saat)
        
        entry_saat.delete(0, tk.END)
        entry_randevu_isim.delete(0, tk.END)
        entry_eposta.delete(0, tk.END)
    except sqlite3.IntegrityError:
        messagebox.showwarning("Uyarı", f"{saat} seansı zaten dolu! Lütfen önce o saati iptal edin.")
    finally:
        conn.close()
        arayuzu_guncelle()

def yedek_hasta_ekle():
    hasta_adi = entry_yeni_hasta.get().strip()
    hasta_mail = entry_yedek_eposta.get().strip()
    durum     = aciliyet_secimi.get()
    tarih = app_state["aktif_tarih"]

    if hasta_adi == "":
        messagebox.showwarning("Uyarı", "Lütfen hastanın adını girin!")
        return
        
    if hasta_mail and not eposta_gecerli_mi(hasta_mail):
        messagebox.showwarning("Uyarı", "Geçersiz e-posta formatı!")
        return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("INSERT INTO yedekler (tarih, hasta_isim, eposta, durum) VALUES (?, ?, ?, ?)", (tarih, hasta_adi, hasta_mail, durum))
    conn.commit()
    conn.close()

    log_yaz(f"Yedek hasta eklendi: {tarih} -> {hasta_adi} ({durum})")
    
    entry_yeni_hasta.delete(0, tk.END)
    entry_yedek_eposta.delete(0, tk.END)
    arayuzu_guncelle()

def randevu_iptal_et():
    secili_randevu = listbox_randevular.curselection()
    if not secili_randevu:
        messagebox.showwarning("Uyarı", "Lütfen iptal edilecek randevuyu seçin")
        return

    secilen_metin = listbox_randevular.get(secili_randevu[0]).strip()
    saat = secilen_metin.split("  -  ")[0].strip()
    tarih = app_state["aktif_tarih"]

    cevap = messagebox.askyesno("Sistem Onayı", f"{tarih} {saat} seansı iptal edilecek. Emin misiniz?")
    if not cevap: return

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()

    # İlgili randevuyu sil
    c.execute("DELETE FROM randevular WHERE tarih=? AND saat=?", (tarih, saat))
    log_yaz(f"Randevu iptal edildi. Tarih: {tarih}, Saat: {saat}")

    # Otonom Atama (O güne ait kuyruktaki ilk hastayı çek)
    # Acil olanları öne almak istersen ORDER BY durum ASC, sira ASC yapabilirsin. 
    # Şimdilik sadece sıraya göre çekiyoruz.
    c.execute("SELECT sira, hasta_isim, eposta FROM yedekler WHERE tarih=? ORDER BY sira LIMIT 1", (tarih,))
    yedek = c.fetchone()

    if yedek:
        sira, y_isim, y_eposta = yedek
        # Yedeklerden sil
        c.execute("DELETE FROM yedekler WHERE sira=?", (sira,))
        # Randevulara ekle
        c.execute("INSERT INTO randevular (tarih, saat, hasta_isim, eposta) VALUES (?, ?, ?, ?)", (tarih, saat, y_isim, y_eposta))
        log_yaz(f"Otonom Atama yapıldı. {saat} seansına '{y_isim}' yerleştirildi.")
        
        if y_eposta:
            eposta_thread(y_eposta, y_isim, saat)
            messagebox.showinfo("Otonom Atama", f"Randevu iptal edildi.\nSistem yerine otomatik olarak [{y_isim}] kişisini atadı ve onay maili gönderildi.")
        else:
            messagebox.showinfo("Otonom Atama", f"Randevu iptal edildi.\nSistem yerine otomatik olarak [{y_isim}] kişisini atadı.\n(E-postası olmadığı için mail atlanıldı)")
    else:
        messagebox.showinfo("Bilgi", "Kuyrukta yedek hasta bulunmadığı için saat boş bırakıldı.")

    conn.commit()
    conn.close()
    arayuzu_guncelle()

def randevu_ara(event=None):
    aranan = entry_arama.get().strip().lower()
    if aranan == "":
        arayuzu_guncelle()
        return

    listbox_randevular.delete(0, tk.END)
    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    # Arama yaparken tüm günlerde ara
    c.execute("SELECT tarih, saat, hasta_isim FROM randevular WHERE LOWER(hasta_isim) LIKE ? OR saat LIKE ? ORDER BY tarih, saat", (f"%{aranan}%", f"%{aranan}%"))
    for r in c.fetchall():
        listbox_randevular.insert(tk.END, f"[{r[0]}] {r[1]} - {r[2]}")
    conn.close()

# Diğer ufak UI fonksiyonları ve düzenleme
def yedek_sil():
    secili = listbox_yedek.curselection()
    if not secili: return
    # Basit bir eşleştirme (Gerçekte ID ile silinmeli)
    secilen_metin = listbox_yedek.get(secili[0]).strip()
    isim = secilen_metin.rsplit(" (", 1)[0]
    
    if messagebox.askyesno("Onay", f"Kuyruktaki '{isim}' silinecek. Emin misiniz?"):
        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        c.execute("DELETE FROM yedekler WHERE hasta_isim=? AND tarih=?", (isim, app_state["aktif_tarih"]))
        conn.commit()
        conn.close()
        arayuzu_guncelle()

def randevu_duzenle(event=None):
    # Liste arama modundayken çift tıklamayı kapatmak iyi olabilir, şimdilik aktif tarih üzerinden gidelim.
    secili = listbox_randevular.curselection()
    if not secili: return

    secilen_metin = listbox_randevular.get(secili[0]).strip()
    try:
        saat, eski_isim = secilen_metin.split("  -  ", 1)
    except ValueError: return  

    pencere_duzenle = ctk.CTkToplevel(pencere)
    pencere_duzenle.title("Randevu Düzenle")
    pencere_duzenle.geometry("350x250")
    pencere_duzenle.grab_set()

    ctk.CTkLabel(pencere_duzenle, text=f"{saat} Randevusunu Düzenle", font=ctk.CTkFont(size=14, weight="bold")).pack(pady=20)
    yeni_isim_entry = ctk.CTkEntry(pencere_duzenle, width=250, height=36)
    yeni_isim_entry.insert(0, eski_isim)  
    yeni_isim_entry.pack(pady=10)

    def kaydet_duzenle():
        yeni_isim = yeni_isim_entry.get().strip()
        if yeni_isim == "": return
        
        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        c.execute("UPDATE randevular SET hasta_isim=? WHERE tarih=? AND saat=?", (yeni_isim, app_state["aktif_tarih"], saat))
        conn.commit()
        conn.close()
        
        log_yaz(f"Randevu düzenlendi: {saat} -> '{eski_isim}' DEĞİŞTİ -> '{yeni_isim}'")
        arayuzu_guncelle()
        pencere_duzenle.destroy()

    ctk.CTkButton(pencere_duzenle, text="KAYDET", command=kaydet_duzenle, fg_color="#2980b9", height=38).pack(pady=15)

def gunu_kapat_ve_rapor_al():
    cevap = messagebox.askyesno("Günü Kapat", "Aktif günün raporu alınıp liste yarın için SIFIRLANACAK. Emin misiniz?")
    if not cevap: return

    tarih = app_state["aktif_tarih"]
    zaman_etiketi = datetime.now().strftime("%Y%m%d_%H%M%S")
    rapor_adi = f"Gunluk_Rapor_{tarih}_{zaman_etiketi}.txt"

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("SELECT saat, hasta_isim FROM randevular WHERE tarih=? ORDER BY saat", (tarih,))
    randevular = c.fetchall()
    c.execute("SELECT hasta_isim, durum FROM yedekler WHERE tarih=? ORDER BY sira", (tarih,))
    yedekler = c.fetchall()

    try:
        with open(rapor_adi, "w", encoding="utf-8") as f:
            f.write("--- SMARTCLINIC GÜNLÜK ÖZET RAPORU ---\n")
            f.write(f"Oluşturulma Tarihi : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"Raporu Alan        : {app_state['aktif_kullanici']}\n")
            f.write(f"Kapatılan Tarih    : {tarih}\n\n")

            f.write("=== DOLU RANDEVULAR ===\n")
            for r in randevular: f.write(f"{r[0]} -> {r[1]}\n")

            f.write("\n=== BEKLEYEN YEDEK KUYRUĞU ===\n")
            for idx, y in enumerate(yedekler): f.write(f"{idx+1}. Sıra: {y[0]} ({y[1]})\n")
    except Exception as e:
        messagebox.showerror("Hata", f"Rapor dosyası yazılamadı: {e}")
        conn.close()
        return

    # Verileri temizle ve ertesi güne geç
    c.execute("DELETE FROM randevular WHERE tarih=?", (tarih,))
    c.execute("DELETE FROM yedekler WHERE tarih=?", (tarih,))
    conn.commit()
    conn.close()
    
    tarih_degistir(1) # Ertesi güne otomatik atla
    log_yaz(f"Gün sonu raporu alındı ve sıfırlandı: {rapor_adi}")
    messagebox.showinfo("Başarılı", f"Rapor oluşturuldu:\n{rapor_adi}")

# --- GİRİŞ VE KULLANICI YÖNETİMİ ---
def giris_yap():
    su_an = time.time()
    if app_state["yanlis_deneme"] >= 5:
        kalan = int(app_state["kilitlenme_bitis"] - su_an)
        if kalan > 0:
            messagebox.showerror("Hesap Kilitlendi", f"Çok fazla yanlış deneme!\nLütfen {kalan} saniye bekleyin.")
            return
        else:
            app_state["yanlis_deneme"] = 0

    kul = entry_kullanici.get().strip()
    sif = entry_sifre.get().strip()
    hashli_sifre = sifre_hashle(sif)

    conn = sqlite3.connect(DB_ISMI)
    c = conn.cursor()
    c.execute("SELECT rol FROM kullanicilar WHERE kullanici_adi=? AND sifre_hash=?", (kul, hashli_sifre))
    kullanici = c.fetchone()
    conn.close()

    if kullanici:
        app_state["aktif_kullanici"] = kul
        app_state["aktif_rol"] = kullanici[0]
        app_state["yanlis_deneme"]   = 0   
        log_yaz("Sisteme başarılı giriş yapıldı.")
        frame_giris.pack_forget()
        frame_ana_uygulama.pack(fill="both", expand=True)
        lbl_karsilama.configure(text=f"Aktif Kullanıcı: {kul.upper()} ({app_state['aktif_rol'].upper()})")
        
        # Rol Yönetimi: Yetkisi olmayan admin butonunu göremez
        if app_state["aktif_rol"] == "admin":
            btn_kayit_panel.pack(pady=10)
        else:
            btn_kayit_panel.pack_forget()

        arayuzu_guncelle()
    else:
        app_state["yanlis_deneme"] += 1
        app_state["kilitlenme_bitis"] = su_an + 30  
        kalan_hak = 5 - app_state["yanlis_deneme"]
        log_yaz(f"Başarısız giriş denemesi! Kullanıcı: {kul} | Kalan hak: {kalan_hak}")
        if kalan_hak > 0:
            messagebox.showerror("Erişim Reddedildi", f"Kullanıcı adı veya şifre hatalı!\nKalan deneme hakkı: {kalan_hak}")
        else:
            messagebox.showerror("Hesap Kilitlendi", "5 yanlış deneme! Hesap 30 saniye kilitlendi.")

def yeni_kayit_penceresi():
    pencere_kayit = ctk.CTkToplevel(pencere)
    pencere_kayit.title("Yeni Kullanıcı Oluştur")
    pencere_kayit.geometry("350x400")
    pencere_kayit.grab_set()

    ctk.CTkLabel(pencere_kayit, text="Kayıt Ekranı", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=20)
    yeni_kul  = ctk.CTkEntry(pencere_kayit, placeholder_text="Yeni Kullanıcı Adı", width=200)
    yeni_kul.pack(pady=10)
    yeni_sifre = ctk.CTkEntry(pencere_kayit, placeholder_text="Yeni Şifre", show="*", width=200)
    yeni_sifre.pack(pady=10)
    
    rol_secimi = ctk.StringVar(value="personel")
    ctk.CTkRadioButton(pencere_kayit, text="Personel", variable=rol_secimi, value="personel").pack(pady=5)
    ctk.CTkRadioButton(pencere_kayit, text="Admin", variable=rol_secimi, value="admin").pack(pady=5)

    def kaydi_tamamla():
        k = yeni_kul.get().strip()
        s = yeni_sifre.get().strip()
        r = rol_secimi.get()
        if k == "" or s == "":
            messagebox.showwarning("Hata", "Boş alan bırakmayın!", parent=pencere_kayit)
            return

        conn = sqlite3.connect(DB_ISMI)
        c = conn.cursor()
        try:
            c.execute("INSERT INTO kullanicilar (kullanici_adi, sifre_hash, rol) VALUES (?, ?, ?)", (k, sifre_hashle(s), r))
            conn.commit()
            log_yaz(f"Sisteme yeni kullanıcı eklendi: {k} Rol: {r}")
            messagebox.showinfo("Başarılı", "Yeni hesap oluşturuldu.", parent=pencere_kayit)
            pencere_kayit.destroy()
        except sqlite3.IntegrityError:
            messagebox.showwarning("Hata", "Bu kullanıcı zaten var!", parent=pencere_kayit)
        finally:
            conn.close()

    ctk.CTkButton(pencere_kayit, text="KAYDET", command=kaydi_tamamla, fg_color="#27ae60").pack(pady=20)

def uygulamayi_kapat():
    if app_state["aktif_kullanici"]:
        log_yaz("Uygulama kapatıldı.")
    pencere.destroy()

# --- BAŞLANGIÇ İŞLEMLERİ ---
try:
    for silinecek in ["randevu_verileri.json", "kullanicilar.json", "randevu_verileri_YEDEK.json"]:
        if os.path.exists(silinecek): os.remove(silinecek)
except Exception as e:
    print(f"Eski dosya temizlik hatası: {e}")

db_kurulum()

# --- ARAYÜZ KURULUMU ---
pencere = ctk.CTk()
pencere.title("SmartClinic Pro v8.0 - SQLite & Güvenlik Mimarisi")
pencere.geometry("1020x820")
pencere.protocol("WM_DELETE_WINDOW", uygulamayi_kapat)  

frame_giris = ctk.CTkFrame(pencere, width=420, height=500, corner_radius=20)
frame_giris.pack(pady=80)

ctk.CTkLabel(frame_giris, text="AKILLI KLİNİK", font=ctk.CTkFont(size=30, weight="bold")).pack(pady=(40, 5))
ctk.CTkLabel(frame_giris, text="Sistem Giriş Paneli", font=ctk.CTkFont(size=14)).pack(pady=(0, 5))

entry_kullanici = ctk.CTkEntry(frame_giris, placeholder_text="Kullanıcı Adı", width=270, height=42)
entry_kullanici.pack(pady=8)
entry_sifre = ctk.CTkEntry(frame_giris, placeholder_text="Şifre", show="*", width=270, height=42)
entry_sifre.pack(pady=8)
entry_sifre.bind("<Return>", lambda e: giris_yap())

btn_giris = ctk.CTkButton(frame_giris, text="GİRİŞ YAP", command=giris_yap, width=270, height=48, corner_radius=10, font=ctk.CTkFont(weight="bold"))
btn_giris.pack(pady=(20, 10))

frame_ana_uygulama = ctk.CTkFrame(pencere, fg_color="transparent")

sol_panel = ctk.CTkFrame(frame_ana_uygulama, width=310, corner_radius=0)
sol_panel.pack(side="left", fill="y")

lbl_karsilama = ctk.CTkLabel(sol_panel, text="Kullanıcı", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f1c40f")
lbl_karsilama.pack(pady=(15, 2))

# Sadece adminler görsün diye butonu burada tanımlıyoruz ama menüde gizli tutuyoruz
btn_kayit_panel = ctk.CTkButton(sol_panel, text="Yeni Personel Ekle", command=yeni_kayit_penceresi, fg_color="#34495e", hover_color="#2c3e50")

ctk.CTkLabel(sol_panel, text="KONTROL MERKEZİ", font=ctk.CTkFont(size=18, weight="bold")).pack(pady=(0, 12))

tarih_frame = ctk.CTkFrame(sol_panel, fg_color="#1a252f", corner_radius=8)
tarih_frame.pack(fill="x", padx=15, pady=(0, 10))

ctk.CTkButton(tarih_frame, text="◀", width=36, height=30, command=lambda: tarih_degistir(-1)).pack(side="left", padx=5, pady=5)
lbl_aktif_tarih = ctk.CTkLabel(tarih_frame, text=app_state["aktif_tarih"], font=ctk.CTkFont(size=12))
lbl_aktif_tarih.pack(side="left", expand=True)
ctk.CTkButton(tarih_frame, text="▶", width=36, height=30, command=lambda: tarih_degistir(1)).pack(side="right", padx=5, pady=5)

ctk.CTkLabel(sol_panel, text="YENİ RANDEVU", font=ctk.CTkFont(size=13, weight="bold"), text_color="#3498db").pack(pady=(8, 0))
entry_saat = ctk.CTkEntry(sol_panel, placeholder_text="Saat (Örn: 14:15)", height=32)
entry_saat.pack(pady=4, padx=20, fill="x")
entry_randevu_isim = ctk.CTkEntry(sol_panel, placeholder_text="Hasta Ad Soyad", height=32)
entry_randevu_isim.pack(pady=4, padx=20, fill="x")
entry_eposta = ctk.CTkEntry(sol_panel, placeholder_text="Hasta E-posta Adresi", height=32)
entry_eposta.pack(pady=4, padx=20, fill="x")

btn_yeni_randevu = ctk.CTkButton(sol_panel, text="RANDEVU OLUŞTUR", command=randevu_olustur, fg_color="#2980b9", hover_color="#1f618d", height=35)
btn_yeni_randevu.pack(pady=(4, 10), padx=20, fill="x")

ctk.CTkFrame(sol_panel, height=2, fg_color="#3d3d3d").pack(fill="x", padx=30, pady=5)

ctk.CTkLabel(sol_panel, text="YEDEK KUYRUK", font=ctk.CTkFont(size=13, weight="bold"), text_color="#27ae60").pack(pady=(8, 0))
entry_yeni_hasta = ctk.CTkEntry(sol_panel, placeholder_text="Hasta Ad Soyad", height=32)
entry_yeni_hasta.pack(pady=4, padx=20, fill="x")
entry_yedek_eposta = ctk.CTkEntry(sol_panel, placeholder_text="Hasta E-posta (Otonom Atama için)", height=32)
entry_yedek_eposta.pack(pady=4, padx=20, fill="x")

aciliyet_secimi = ctk.StringVar(value="Normal")
ctk.CTkRadioButton(sol_panel, text="Normal Liste", variable=aciliyet_secimi, value="Normal").pack(pady=4, padx=30, anchor="w")
ctk.CTkRadioButton(sol_panel, text="Acil (Öncelikli)", variable=aciliyet_secimi, value="Acil", text_color="#e74c3c").pack(pady=4, padx=30, anchor="w")

btn_ekle = ctk.CTkButton(sol_panel, text="KUYRUĞA EKLE", command=yedek_hasta_ekle, fg_color="#27ae60", hover_color="#1e8449", height=35)
btn_ekle.pack(pady=(4, 12), padx=20, fill="x")

ctk.CTkFrame(sol_panel, height=2, fg_color="#3d3d3d").pack(fill="x", padx=30, pady=5)

btn_iptal = ctk.CTkButton(sol_panel, text="RANDEVU İPTAL ET\n(Otonom Yedekle)", command=randevu_iptal_et, fg_color="#c0392b", hover_color="#962d22", height=50, font=ctk.CTkFont(weight="bold"))
btn_iptal.pack(pady=10, padx=20, fill="x")

btn_rapor = ctk.CTkButton(sol_panel, text="GÜNÜ KAPAT & RAPOR AL", command=gunu_kapat_ve_rapor_al, fg_color="#8e44ad", hover_color="#732d91", height=40)
btn_rapor.pack(side="bottom", pady=20, padx=20, fill="x")

# --- SAĞ PANEL ---
sag_panel = ctk.CTkFrame(frame_ana_uygulama, fg_color="transparent")
sag_panel.pack(side="right", fill="both", expand=True, padx=30, pady=20)

# SENSÖR PANELİ EKLENDİ
sensor_frame = ctk.CTkFrame(sag_panel, fg_color="#2c3e50", corner_radius=10, height=50)
sensor_frame.pack(fill="x", pady=(0, 12), ipady=5)
ctk.CTkLabel(sensor_frame, text="🌡️ AKILLI ATEŞ ÖLÇER:", font=ctk.CTkFont(size=14, weight="bold"), text_color="#ecf0f1").pack(side="left", padx=15)
lbl_sensor = ctk.CTkLabel(sensor_frame, text="Başlatılıyor...", font=ctk.CTkFont(size=14, weight="bold"), text_color="#f1c40f")
lbl_sensor.pack(side="right", padx=20)

stat_frame = ctk.CTkFrame(sag_panel, fg_color="#1c2833", corner_radius=10, height=50)
stat_frame.pack(fill="x", pady=(0, 12), ipady=10)

lbl_stat_randevu = ctk.CTkLabel(stat_frame, text="Dolu Seans: 0", font=ctk.CTkFont(size=14, weight="bold"), text_color="#3498db")
lbl_stat_randevu.pack(side="left", padx=30)

lbl_stat_yedek = ctk.CTkLabel(stat_frame, text="Kuyruk: 0", font=ctk.CTkFont(size=14, weight="bold"), text_color="#e67e22")
lbl_stat_yedek.pack(side="right", padx=30)

arama_frame = ctk.CTkFrame(sag_panel, fg_color="transparent")
arama_frame.pack(fill="x", pady=(0, 8))

ctk.CTkLabel(arama_frame, text="🔍", font=ctk.CTkFont(size=15)).pack(side="left", padx=(0, 6))
entry_arama = ctk.CTkEntry(arama_frame, placeholder_text="Hasta adı veya saat ara...", height=32)
entry_arama.pack(side="left", fill="x", expand=True)
entry_arama.bind("<KeyRelease>", randevu_ara)

ctk.CTkLabel(sag_panel, text="GÜNLÜK RANDEVU PLANI", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(0, 4))

listbox_randevular = tk.Listbox(sag_panel, height=8, bg="#1e1e1e", fg="#ecf0f1", font=("Consolas", 13), borderwidth=0, highlightthickness=1, highlightbackground="#34495e", selectbackground="#3498db", exportselection=False)
listbox_randevular.pack(fill="x", pady=(0, 4))
listbox_randevular.bind("<Double-Button-1>", randevu_duzenle)

ctk.CTkLabel(sag_panel, text="BEKLEYEN YEDEK LİSTESİ", font=ctk.CTkFont(size=16, weight="bold")).pack(anchor="w", pady=(12, 4))

listbox_yedek = tk.Listbox(sag_panel, height=7, bg="#1e1e1e", fg="#ecf0f1", font=("Consolas", 13), borderwidth=0, highlightthickness=1, highlightbackground="#34495e", selectbackground="#27ae60", exportselection=False)
listbox_yedek.pack(fill="x")

btn_yedek_sil = ctk.CTkButton(sag_panel, text="Seçili Yedeği Listeden Sil", command=yedek_sil, fg_color="transparent", hover_color="#4d0000", border_width=1, border_color="#e74c3c", text_color="#e74c3c")
btn_yedek_sil.pack(anchor="e", pady=10)

# --- SENSÖR DİNLEME (Zarif Düşme ile) ---
sensor_port = None
if SERIAL_AVAILABLE:
    try:
        sensor_port = serial.Serial(SENSOR_PORT_AYARI, 9600, timeout=0.1)
        lbl_sensor.configure(text="Bağlantı Bekleniyor...", text_color="#f1c40f")
    except Exception:
        sensor_port = None
        lbl_sensor.configure(text="Port Yapılandırılmadı", text_color="#e74c3c")
else:
    lbl_sensor.configure(text="pyserial Kurulu Değil", text_color="gray")

def sensoru_dinle():
    if sensor_port and sensor_port.is_open:
        try:
            if sensor_port.in_waiting > 0:
                veri = sensor_port.readline().decode('utf-8').strip()
                if veri.startswith("ATES:"):
                    derece = float(veri.split(":")[1])
                    renk = "#2ecc71" if derece <= 37.5 else "#e74c3c"
                    lbl_sensor.configure(text=f"{derece} °C", text_color=renk)
        except Exception:
            pass
    pencere.after(1000, sensoru_dinle)

sensoru_dinle()

pencere.mainloop()