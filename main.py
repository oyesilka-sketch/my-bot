from flask import Flask, render_template, request, jsonify, session, redirect, url_for, flash, send_from_directory
import requests
from bs4 import BeautifulSoup
import json
import re
import time
import os
import random
from datetime import datetime
import google.generativeai as genai
from requests.auth import HTTPBasicAuth
import haber_kaynaklari
from PIL import Image
import threading
from pathlib import Path
from werkzeug.utils import secure_filename
import uuid
from pyngrok import ngrok, conf
import atexit
import signal
import sys

app = Flask(__name__)
app.secret_key = 'haber_yonetim_secret_key_2024'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'static/images'

# Ngrok yapılandırması
NGROK_AUTH_TOKEN = '31VLc3RYqaikIfktxsWr9fwU9jD_66ZiQCgTiyXaQWXFKSDbc'
NGROK_TUNNEL = None

# İzin verilen dosya türleri
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def setup_ngrok():
    """Ngrok kurulumu ve başlatma"""
    global NGROK_TUNNEL, NGROK_AUTH_TOKEN
    
    try:
        # Config'den ngrok token'ı al
        if os.path.exists('config.json'):
            with open('config.json', 'r', encoding='utf-8') as f:
                config = json.load(f)
                NGROK_AUTH_TOKEN = config.get('ngrok', {}).get('auth_token')
        
        if NGROK_AUTH_TOKEN:
            # Ngrok auth token'ı set et
            conf.get_default().auth_token = NGROK_AUTH_TOKEN
            print("Ngrok auth token set edildi.")
        else:
            print("Uyarı: Ngrok auth token bulunamadı. Free tier sınırlamaları geçerli olacak.")
        
        # Mevcut tunnel'ları kapat
        ngrok.kill()
        
        # Yeni tunnel başlat
        NGROK_TUNNEL = ngrok.connect(5000, bind_tls=True)
        ngrok_url = NGROK_TUNNEL.public_url
        
        print("\n" + "="*60)
        print("🌐 NGROK TUNNEL AKTİF")
        print("="*60)
        print(f"Local URL: http://localhost:5000")
        print(f"Public URL: {ngrok_url}")
        print(f"iPhone/Mobil URL: {ngrok_url}")
        print("="*60)
        print("📱 iPhone'dan bu URL'i Safari'de açın!")
        print("⚠️  Güvenlik: Bu URL'i kimseyle paylaşmayın")
        print("="*60 + "\n")
        
        return ngrok_url
        
    except Exception as e:
        print(f"Ngrok kurulum hatası: {e}")
        print("Ngrok olmadan local modda çalışacak...")
        return None

def cleanup_ngrok():
    """Ngrok temizleme"""
    try:
        if NGROK_TUNNEL:
            ngrok.disconnect(NGROK_TUNNEL.public_url)
        ngrok.kill()
        print("Ngrok tunnel kapatıldı.")
    except:
        pass

def signal_handler(sig, frame):
    """Çıkış sinyali yakalama"""
    print("\nUygulama kapatılıyor...")
    cleanup_ngrok()
    sys.exit(0)

# Çıkış işleyicilerini kaydet
atexit.register(cleanup_ngrok)
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

class HaberYoneticisi:
    def __init__(self):
        self.load_config()
        self.haberler = []
        self.secili_haber = None
        
    def load_config(self):
        """Konfigürasyon yükleme"""
        try:
            with open('config.json', 'r', encoding='utf-8') as f:
                self.config = json.load(f)
                
            # WordPress ayarları
            self.WORDPRESS_USERNAME = self.config['wordpress']['username']
            self.WORDPRESS_PASSWORD = self.config['wordpress']['password'] 
            self.WORDPRESS_URL = self.config['wordpress']['url']
            self.WP_AUTH = HTTPBasicAuth(self.WORDPRESS_USERNAME, self.WORDPRESS_PASSWORD)
            
            # AI ayarları
            self.GOOGLE_AI_KEY = self.config['google_ai']['api_key']
            genai.configure(api_key=self.GOOGLE_AI_KEY)
            
            # KLASÖR UYUMLULUĞU İÇİN DÜZELTME
            # Flask app.config ile aynı klasörü kullan
            self.IMAGE_FOLDER = app.config['UPLOAD_FOLDER']  # 'static/images' kullan
            
            print(f"DEBUG CONFIG: IMAGE_FOLDER ayarlandı: {self.IMAGE_FOLDER}")
            print(f"DEBUG CONFIG: Flask UPLOAD_FOLDER: {app.config['UPLOAD_FOLDER']}")
            
            # Config dosyasındaki diğer ayarlar
            config_settings = self.config.get('settings', {})
            self.ONCEKI_HABERLER_FILE = config_settings.get('onceki_haberler_file', 'data/onceki_istanbul_haberler.json')
            self.ONCEKI_GUNCEL_HABERLER_FILE = config_settings.get('onceki_guncel_haberler_file', 'data/onceki_guncel_haberler.json')
            
        except Exception as e:
            print(f"Config yükleme hatası: {e}")
            # Fallback values
            self.IMAGE_FOLDER = app.config['UPLOAD_FOLDER']
            self.ONCEKI_HABERLER_FILE = 'data/onceki_istanbul_haberler.json'
            self.ONCEKI_GUNCEL_HABERLER_FILE = 'data/onceki_guncel_haberler.json'
            
    def haberleri_yenile(self):
        """Haberleri yeniden çek"""
        try:
            # Önceki haberleri yükle
            onceki_istanbul = self.onceki_haberler_yukle(self.ONCEKI_HABERLER_FILE)
            onceki_guncel = self.onceki_haberler_yukle(self.ONCEKI_GUNCEL_HABERLER_FILE)
            
            # Yeni haberleri kontrol et
            yeni_istanbul, yeni_guncel, yeni_gelen, kaynak = haber_kaynaklari.sirali_haber_kontrol(
                self.config, onceki_istanbul, onceki_guncel
            )
            
            # Haberleri birleştir
            self.haberler = []
            
            # Son 50 İstanbul haberi
            for haber in yeni_istanbul[-50:]:
                haber['kaynak'] = 'İstanbul'
                haber['durum'] = 'Yeni' if haber in yeni_gelen else 'Eski'
                haber['id'] = str(uuid.uuid4())[:8]
                self.haberler.append(haber)
            
            # Son 50 Güncel haber  
            for haber in yeni_guncel[-50:]:
                haber['kaynak'] = 'Güncel'
                haber['durum'] = 'Yeni' if haber in yeni_gelen else 'Eski'
                haber['id'] = str(uuid.uuid4())[:8]
                self.haberler.append(haber)
            
            return True, len(yeni_gelen)
            
        except Exception as e:
            print(f"Haber çekme hatası: {e}")
            return False, 0
            
    def ai_ile_yeniden_yaz(self, haber):
        """AI ile haberi SEO uyumlu olarak yeniden yaz"""
        try:
            haber_metni = haber.get('haber_metni', '')
            baslik = haber.get('baslik', haber.get('headline', ''))
            description = haber.get('description', '')
            
            # AI prompt
            prompt = f"""
            Aşağıdaki haber metnini 600-700 kelime arasında, SEO uyumlu ve profesyonel bir şekilde yeniden yaz.

            ÖNEMLİ KURALLAR:
            - 600-700 kelime arasında olmalı
            - SEO uyumlu başlıklar kullan (H1, H2, H3)
            - Ana anahtar kelimeyi başlıkta ve metin içinde kullan
            - İçindekiler tablosu ekle
            - Paragraflar arası geçişler doğal olsun
            - Özgün içerik üret, kopyala-yapıştır yapma
            - HTML formatında döndür

            SADECE JSON formatında döndür:
            {{
              "icerik": "HTML içeriği - h1 başlık, içindekiler, h2 alt başlıklar ve paragraflar",
              "etiketler": ["8-12 adet SEO uyumlu etiket"],
              "kelime_sayisi": kelime_sayısı
            }}

            HTML formatı:
            - <h1> ana başlık
            - <div class="wp-block-yoast-seo-table-of-contents yoast-table-of-contents"><h2>İçindekiler</h2><ul><li><a href="#h-baslik" data-level="2">Başlık</a></li></ul></div>
            - <h2 id="h-baslik"> alt başlıklar
            - <h3 id="h-alt-baslik"> daha alt başlıklar
            - Her bölüm 2-3 paragraf
            - <p> paragraflar için

            BAŞLIK: {baslik}
            AÇIKLAMA: {description}
            HABER METNİ: {haber_metni}
            """
            
            model = genai.GenerativeModel("gemini-2.0-flash-exp")
            response = model.generate_content(prompt)
            
            # JSON temizle ve parse et
            ai_yazip = response.text.strip()
            ai_yazip = self.json_temizle(ai_yazip)
            
            ai_data = json.loads(ai_yazip)
            
            return {
                'success': True,
                'icerik': ai_data.get('icerik', ''),
                'etiketler': ai_data.get('etiketler', []),
                'kelime_sayisi': ai_data.get('kelime_sayisi', 0)
            }
            
        except Exception as e:
            return {'success': False, 'error': str(e)}
            
    def json_temizle(self, ai_yazip):
        """JSON metnini temizle"""
        if not ai_yazip:
            return ""
        
        icerik = ai_yazip.strip()
        
        # Başlangıç temizleme
        for kaldir in ['```json\n', '```json', '```\n', '```', '`\n', '`']:
            if icerik.startswith(kaldir):
                icerik = icerik[len(kaldir):]
                break
        
        # Son temizleme
        for kaldir in ['\n```', '```', '\n`', '`']:
            if icerik.endswith(kaldir):
                icerik = icerik[:-len(kaldir)]
                break
        
        return icerik.strip()
        
    def onceki_haberler_yukle(self, dosya):
        """Önceki haberleri yükle"""
        if os.path.exists(dosya):
            try:
                with open(dosya, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                return []
        return []
        
    def kategorileri_yukle(self):
        """WordPress kategorilerini yükle"""
        try:
            response = requests.get(f'{self.WORDPRESS_URL}/wp-json/wp/v2/categories', 
                                  auth=self.WP_AUTH, params={'per_page': 100})
            
            if response.status_code == 200:
                return response.json()
            return []
        except Exception as e:
            print(f"Kategori yükleme hatası: {e}")
            return []
            
    def haberi_yayinla(self, baslik, icerik, etiketler, kategori_id=None, resim_yolu=None):
        """Haberi WordPress'e yayınla"""
        try:
            print(f"DEBUG: Yayın başlıyor...")
            print(f"DEBUG: Başlık: {baslik}")
            print(f"DEBUG: Resim yolu: {resim_yolu}")
            
            # Kapak fotoğrafı yükle
            kapak_fotografi_id = None
            if resim_yolu and os.path.exists(resim_yolu):
                print(f"DEBUG: Resim dosyası bulundu: {resim_yolu}")
                print(f"DEBUG: Dosya boyutu: {os.path.getsize(resim_yolu)} bytes")
                kapak_fotografi_id = self.wordpress_medya_yukle(resim_yolu)
                print(f"DEBUG: WordPress medya ID: {kapak_fotografi_id}")
            else:
                print(f"DEBUG: Resim dosyası bulunamadı veya yol boş: {resim_yolu}")
                if resim_yolu:
                    print(f"DEBUG: Dosya var mı kontrol: {os.path.exists(resim_yolu)}")
                    if os.path.exists(os.path.dirname(resim_yolu)):
                        print(f"DEBUG: Klasör içeriği: {os.listdir(os.path.dirname(resim_yolu))}")
            
            # Etiket ID'lerini al
            tag_ids = []
            for etiket in etiketler:
                tag_id = self.etiket_olustur_veya_bul(etiket)
                if tag_id:
                    tag_ids.append(tag_id)
            
            data = {
                'title': baslik,
                'content': icerik,
                'status': 'publish',
                'tags': tag_ids
            }
            
            if kategori_id:
                data['categories'] = [kategori_id]
            
            if kapak_fotografi_id:
                data['featured_media'] = kapak_fotografi_id
                print(f"DEBUG: Featured media ID eklendi: {kapak_fotografi_id}")
            else:
                print("DEBUG: Kapak fotoğrafı ID'si yok!")
            
            print(f"DEBUG: WordPress'e gönderilecek data: {data}")
            
            response = requests.post(f'{self.WORDPRESS_URL}/wp-json/wp/v2/posts', 
                                   auth=self.WP_AUTH, json=data)
            
            print(f"DEBUG: WordPress response status: {response.status_code}")
            
            if response.status_code == 201:
                post_data = response.json()
                print(f"DEBUG: Post başarıyla oluşturuldu: {post_data.get('id')}")
                
                # BAŞARILI YAYINDAN SONRA RESMİ SİL
                if resim_yolu and os.path.exists(resim_yolu) and kapak_fotografi_id:
                    try:
                        os.remove(resim_yolu)
                        print(f"DEBUG: Resim dosyası silindi: {resim_yolu}")
                    except Exception as delete_error:
                        print(f"DEBUG: Resim silme hatası: {delete_error}")
                        # Silme hatası yayını etkilemez, devam et
                
                return {'success': True, 'link': post_data.get('link', 'Link alınamadı')}
            else:
                print(f"DEBUG: WordPress error response: {response.text}")
                # Yayın başarısız olursa resmi silme
                return {'success': False, 'error': f"HTTP {response.status_code}: {response.text}"}
                
        except Exception as e:
            print(f"DEBUG: Exception in haberi_yayinla: {str(e)}")
            # Exception durumunda da resmi silme
            return {'success': False, 'error': str(e)}
            
    def wordpress_medya_yukle(self, dosya_yolu):
        """WordPress'e medya yükle"""
        try:
            print(f"DEBUG: WordPress medya yükleme başlıyor: {dosya_yolu}")
            
            if not os.path.exists(dosya_yolu):
                print(f"DEBUG: Dosya bulunamadı: {dosya_yolu}")
                return None
            
            file_size = os.path.getsize(dosya_yolu)
            print(f"DEBUG: Dosya boyutu: {file_size} bytes")
            
            mime_types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', 
                         '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
            
            dosya_adi = os.path.basename(dosya_yolu)
            uzanti = os.path.splitext(dosya_adi)[1].lower()
            content_type = mime_types.get(uzanti, 'image/jpeg')
            
            print(f"DEBUG: Dosya adı: {dosya_adi}")
            print(f"DEBUG: Uzantı: {uzanti}")
            print(f"DEBUG: Content type: {content_type}")
            
            with open(dosya_yolu, 'rb') as f:
                file_data = f.read()
                print(f"DEBUG: Dosya okundu, boyut: {len(file_data)} bytes")
                
                headers = {
                    'Content-Type': content_type,
                    'Content-Disposition': f'attachment; filename="{dosya_adi}"'
                }
                
                print(f"DEBUG: WordPress medya URL: {self.WORDPRESS_URL}/wp-json/wp/v2/media")
                print(f"DEBUG: Headers: {headers}")
                
                response = requests.post(f'{self.WORDPRESS_URL}/wp-json/wp/v2/media', 
                                       auth=self.WP_AUTH, headers=headers, data=file_data)
                
                print(f"DEBUG: WordPress medya response status: {response.status_code}")
                
                if response.status_code == 201:
                    response_data = response.json()
                    media_id = response_data.get('id')
                    print(f"DEBUG: WordPress medya başarıyla yüklendi, ID: {media_id}")
                    print(f"DEBUG: Medya URL: {response_data.get('source_url', 'N/A')}")
                    return media_id
                else:
                    print(f"DEBUG: WordPress medya yükleme hatası: {response.status_code}")
                    print(f"DEBUG: Error response: {response.text}")
                    return None
            
        except Exception as e:
            print(f"DEBUG: WordPress medya yükleme exception: {str(e)}")
            return None
            
    def etiket_olustur_veya_bul(self, etiket_adi):
        """Etiket oluştur veya bul"""
        try:
            # Önce var mı kontrol et
            response = requests.get(f'{self.WORDPRESS_URL}/wp-json/wp/v2/tags', 
                                   auth=self.WP_AUTH, params={'search': etiket_adi})
            
            if response.status_code == 200:
                for tag in response.json():
                    if tag['name'].lower() == etiket_adi.lower():
                        return tag['id']
            
            # Yoksa oluştur
            create_response = requests.post(f'{self.WORDPRESS_URL}/wp-json/wp/v2/tags', 
                                           auth=self.WP_AUTH, json={'name': etiket_adi})
            
            if create_response.status_code == 201:
                return create_response.json()['id']
            
            return None
        except:
            return None
    
    def eski_resimleri_temizle(self, max_yas_saat=24):
        """Eski yüklenen resimleri temizle (varsayılan: 24 saat)"""
        try:
            if not os.path.exists(self.IMAGE_FOLDER):
                print("DEBUG CLEANUP: Image folder bulunamadı")
                return
            
            import time
            from datetime import datetime, timedelta
            
            simdiki_zaman = time.time()
            max_yas_saniye = max_yas_saat * 3600
            silinen_dosyalar = []
            
            print(f"DEBUG CLEANUP: {max_yas_saat} saatten eski dosyalar temizleniyor...")
            
            for dosya in os.listdir(self.IMAGE_FOLDER):
                dosya_yolu = os.path.join(self.IMAGE_FOLDER, dosya)
                
                if os.path.isfile(dosya_yolu):
                    dosya_zamani = os.path.getmtime(dosya_yolu)
                    dosya_yasi = simdiki_zaman - dosya_zamani
                    
                    if dosya_yasi > max_yas_saniye:
                        try:
                            os.remove(dosya_yolu)
                            silinen_dosyalar.append(dosya)
                            print(f"DEBUG CLEANUP: Eski dosya silindi: {dosya}")
                        except Exception as e:
                            print(f"DEBUG CLEANUP: Dosya silme hatası {dosya}: {e}")
            
            if silinen_dosyalar:
                print(f"DEBUG CLEANUP: {len(silinen_dosyalar)} eski dosya temizlendi")
            else:
                print("DEBUG CLEANUP: Silinecek eski dosya bulunamadı")
                
            return len(silinen_dosyalar)
            
        except Exception as e:
            print(f"DEBUG CLEANUP: Cleanup hatası: {e}")
            return 0

# Global yönetici instance
yonetici = HaberYoneticisi()

@app.route('/')
def ana_sayfa():
    """Ana sayfa - haber listesi"""
    filtre = request.args.get('filtre', 'Tümü')
    
    # Haberleri filtrele
    if filtre == "Yeni":
        haberler = [h for h in yonetici.haberler if h.get('durum') == 'Yeni']
    elif filtre == "İstanbul":
        haberler = [h for h in yonetici.haberler if h.get('kaynak') == 'İstanbul']
    elif filtre == "Güncel":
        haberler = [h for h in yonetici.haberler if h.get('kaynak') == 'Güncel']
    else:
        haberler = yonetici.haberler
    
    # İstatistikler
    toplam_haber = len(yonetici.haberler)
    yeni_haber = len([h for h in yonetici.haberler if h.get('durum') == 'Yeni'])
    
    return render_template('ana_sayfa.html', 
                         haberler=haberler, 
                         filtre=filtre,
                         toplam_haber=toplam_haber,
                         yeni_haber=yeni_haber)

@app.route('/haber/<haber_id>')
def haber_detay(haber_id):
    """Haber detay/editör sayfası"""
    haber = None
    for h in yonetici.haberler:
        if h.get('id') == haber_id:
            haber = h
            break
    
    if not haber:
        flash('Haber bulunamadı!', 'error')
        return redirect(url_for('ana_sayfa'))
    
    # Kategorileri yükle
    kategoriler = yonetici.kategorileri_yukle()
    
    # Resim dosyalarını listele
    resim_dosyalari = []
    if os.path.exists(yonetici.IMAGE_FOLDER):
        formatlar = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
        resim_dosyalari = [f for f in os.listdir(yonetici.IMAGE_FOLDER) 
                          if os.path.splitext(f)[1].lower() in formatlar]
    
    return render_template('haber_detay.html', 
                         haber=haber, 
                         kategoriler=kategoriler,
                         resim_dosyalari=resim_dosyalari)

@app.route('/api/haberleri-yenile', methods=['POST'])
def api_haberleri_yenile():
    """API: Haberleri yenile"""
    try:
        basarili, yeni_sayisi = yonetici.haberleri_yenile()
        
        if basarili:
            return jsonify({
                'success': True, 
                'message': f'Haberler güncellendi. {yeni_sayisi} yeni haber bulundu.',
                'toplam_haber': len(yonetici.haberler),
                'yeni_haber': yeni_sayisi
            })
        else:
            return jsonify({'success': False, 'error': 'Haberler güncellenemedi'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/kelime-sayisi', methods=['POST'])
def api_kelime_sayisi():
    """API: Kelime sayısını hesapla"""
    try:
        data = request.get_json()
        icerik = data.get('icerik', '')
        
        # HTML taglarını kaldır
        temiz_metin = re.sub(r'<[^>]+>', '', icerik)
        kelimeler = len(temiz_metin.split())
        
        return jsonify({'kelime_sayisi': kelimeler})
        
    except Exception as e:
        return jsonify({'kelime_sayisi': 0})

@app.route('/api/ai-yeniden-yaz', methods=['POST'])
def api_ai_yeniden_yaz():
    """API: AI ile haberi yeniden yaz"""
    try:
        data = request.get_json()
        haber_id = data.get('haber_id')
        
        # Haberi bul
        haber = None
        for h in yonetici.haberler:
            if h.get('id') == haber_id:
                haber = h
                break
        
        if not haber:
            return jsonify({'success': False, 'error': 'Haber bulunamadı'})
        
        # AI ile yeniden yaz
        sonuc = yonetici.ai_ile_yeniden_yaz(haber)
        return jsonify(sonuc)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/haberi-yayinla', methods=['POST'])
def api_haberi_yayinla():
    """API: Haberi yayınla"""
    try:
        data = request.get_json()
        
        baslik = data.get('baslik', '')
        icerik = data.get('icerik', '')
        etiketler = data.get('etiketler', [])
        kategori_id = data.get('kategori_id')
        resim_dosyasi = data.get('resim_dosyasi')
        
        print(f"DEBUG API: Gelen data:")
        print(f"DEBUG API: Başlık: {baslik}")
        print(f"DEBUG API: Resim dosyası: {resim_dosyasi}")
        print(f"DEBUG API: IMAGE_FOLDER: {yonetici.IMAGE_FOLDER}")
        print(f"DEBUG API: Current working directory: {os.getcwd()}")
        print(f"DEBUG API: Environment: {os.environ.get('RENDER', 'LOCAL')}")
        
        if not baslik or not icerik:
            return jsonify({'success': False, 'error': 'Başlık ve içerik gerekli'})
        
        # Fotoğraf kontrolü - ZORUNLU
        if not resim_dosyasi:
            return jsonify({'success': False, 'error': 'Kapak fotoğrafı seçmek zorunludur!'})
        
        # Render.com kontrol
        if os.environ.get('RENDER'):
            print(f"DEBUG API: RENDER ortamında çalışıyor")
            # Render'da dosya sistemi ephemeral - kontrol et
            print(f"DEBUG API: Disk kullanımı kontrol ediliyor...")
            import shutil
            total, used, free = shutil.disk_usage('.')
            print(f"DEBUG API: Free space: {free / (1024**2):.2f} MB")
        
        # Resim yolunu belirle - BU KISIM ÖNEMLİ
        resim_yolu = os.path.join(yonetici.IMAGE_FOLDER, resim_dosyasi)
        print(f"DEBUG API: Oluşturulan resim yolu: {resim_yolu}")
        print(f"DEBUG API: Mutlak yol: {os.path.abspath(resim_yolu)}")
        
        # Klasör varlığını kontrol et
        if not os.path.exists(yonetici.IMAGE_FOLDER):
            print(f"DEBUG API: IMAGE_FOLDER mevcut değil, oluşturuluyor: {yonetici.IMAGE_FOLDER}")
            os.makedirs(yonetici.IMAGE_FOLDER, exist_ok=True)
        
        # Klasör içeriğini listele
        if os.path.exists(yonetici.IMAGE_FOLDER):
            folder_contents = os.listdir(yonetici.IMAGE_FOLDER)
            print(f"DEBUG API: IMAGE_FOLDER içeriği: {folder_contents}")
            print(f"DEBUG API: Klasör içinde {len(folder_contents)} dosya var")
            
            # Dosya adı eşleşmesi kontrol et
            matching_files = [f for f in folder_contents if resim_dosyasi in f or f in resim_dosyasi]
            print(f"DEBUG API: Eşleşen dosyalar: {matching_files}")
        
        if not os.path.exists(resim_yolu):
            print(f"DEBUG API: Resim dosyası bulunamadı!")
            print(f"DEBUG API: Aranan yol: {resim_yolu}")
            
            # Render.com özel kontrolü
            if os.environ.get('RENDER'):
                print(f"DEBUG API: RENDER.COM UYARISI - Ephemeral file system!")
                print(f"DEBUG API: Dosyalar sunucu restart'ında silinir!")
                
                # Alternatif çözüm: Dosya adını bulma
                if os.path.exists(yonetici.IMAGE_FOLDER):
                    all_files = os.listdir(yonetici.IMAGE_FOLDER)
                    # Timestamp kısmını çıkararak eşleşme ara
                    base_name = resim_dosyasi.split('_', 2)[-1] if '_' in resim_dosyasi else resim_dosyasi
                    for file in all_files:
                        if base_name in file or file.endswith(base_name):
                            alternative_path = os.path.join(yonetici.IMAGE_FOLDER, file)
                            print(f"DEBUG API: Alternatif dosya bulundu: {file}")
                            resim_yolu = alternative_path
                            break
            
            if not os.path.exists(resim_yolu):
                return jsonify({
                    'success': False, 
                    'error': f'Fotoğraf bulunamadı! Render.com ephemeral storage sorunu olabilir. Dosyayı yeniden yükleyin.',
                    'debug_info': {
                        'aranan_dosya': resim_dosyasi,
                        'aranan_yol': resim_yolu,
                        'mevcut_dosyalar': folder_contents if 'folder_contents' in locals() else [],
                        'render_ortami': bool(os.environ.get('RENDER'))
                    }
                })
        
        print(f"DEBUG API: Resim dosyası bulundu, boyut: {os.path.getsize(resim_yolu)} bytes")
        
        # Yayınla
        sonuc = yonetici.haberi_yayinla(baslik, icerik, etiketler, kategori_id, resim_yolu)
        
        print(f"DEBUG API: Yayın sonucu: {sonuc}")
        return jsonify(sonuc)
        
    except Exception as e:
        print(f"DEBUG API: Exception: {str(e)}")
        import traceback
        print(f"DEBUG API: Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/static/images/<filename>')
def uploaded_file(filename):
    """Yüklenen resimleri serve et"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/fotograf-yukle', methods=['POST'])
def api_fotograf_yukle():
    """API: Fotoğraf yükle - Render.com uyumlu"""
    try:
        print(f"DEBUG UPLOAD: Fotoğraf yükleme başlıyor...")
        print(f"DEBUG UPLOAD: Flask UPLOAD_FOLDER: {app.config['UPLOAD_FOLDER']}")
        print(f"DEBUG UPLOAD: Yönetici IMAGE_FOLDER: {yonetici.IMAGE_FOLDER}")
        print(f"DEBUG UPLOAD: Render ortamı: {bool(os.environ.get('RENDER'))}")
        
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Dosya seçilmedi'})
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Dosya seçilmedi'})
        
        print(f"DEBUG UPLOAD: Gelen dosya: {file.filename}")
        print(f"DEBUG UPLOAD: Dosya boyutu: {len(file.read())} bytes")
        file.seek(0)  # Dosya pointer'ını başa al
        
        if file and allowed_file(file.filename):
            # Render.com için özel dosya adı stratejisi
            original_filename = secure_filename(file.filename)
            
            if os.environ.get('RENDER'):
                # Render.com'da çok kısa ve basit dosya adı kullan
                timestamp = datetime.now().strftime('%H%M%S')  # Sadece saat-dakika-saniye
                random_suffix = str(random.randint(100, 999))
                extension = os.path.splitext(original_filename)[1].lower()
                filename = f"img_{timestamp}_{random_suffix}{extension}"
            else:
                # Local'de normal timestamp
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
                filename = timestamp + original_filename
            
            print(f"DEBUG UPLOAD: Yeni dosya adı: {filename}")
            
            # TEK KLASÖR KULLAN - TUTARLILIK İÇİN
            upload_folder = app.config['UPLOAD_FOLDER']  # Her zaman aynı klasör
            print(f"DEBUG UPLOAD: Kullanılacak klasör: {upload_folder}")
            
            # Klasörü kesinlikle oluştur
            os.makedirs(upload_folder, exist_ok=True)
            print(f"DEBUG UPLOAD: Klasör oluşturuldu/var: {upload_folder}")
            
            # Render.com için disk kontrolü
            if os.environ.get('RENDER'):
                import shutil
                total, used, free = shutil.disk_usage('.')
                free_mb = free / (1024**2)
                print(f"DEBUG UPLOAD: Render disk - Free: {free_mb:.2f} MB")
                
                if free_mb < 50:  # 50MB'dan az boş yer
                    print(f"DEBUG UPLOAD: Düşük disk alanı, eski dosyalar temizleniyor...")
                    # Render'da eski dosyaları temizle
                    try:
                        for old_file in os.listdir(upload_folder):
                            if old_file.startswith('img_'):  # Sadece yüklenen resimleri sil
                                old_path = os.path.join(upload_folder, old_file)
                                os.remove(old_path)
                                print(f"DEBUG UPLOAD: Eski dosya silindi: {old_file}")
                    except Exception as cleanup_error:
                        print(f"DEBUG UPLOAD: Cleanup hatası: {cleanup_error}")
            
            # Dosyayı kaydet
            filepath = os.path.join(upload_folder, filename)
            print(f"DEBUG UPLOAD: Dosya kaydedilecek yol: {filepath}")
            print(f"DEBUG UPLOAD: Mutlak yol: {os.path.abspath(filepath)}")
            
            try:
                file.save(filepath)
                print(f"DEBUG UPLOAD: Dosya başarıyla kaydedildi")
                
                # Hemen dosya varlığını kontrol et
                if not os.path.exists(filepath):
                    raise Exception(f"Dosya kaydedildi ama hemen bulunamadı: {filepath}")
                
                file_size = os.path.getsize(filepath)
                print(f"DEBUG UPLOAD: Kaydedilen dosya boyutu: {file_size} bytes")
                
                # Klasör içeriğini kontrol et
                folder_contents = os.listdir(upload_folder)
                print(f"DEBUG UPLOAD: Klasör içeriği: {folder_contents}")
                print(f"DEBUG UPLOAD: Yeni dosya listede var mı: {filename in folder_contents}")
                
            except Exception as save_error:
                print(f"DEBUG UPLOAD: Dosya kaydetme hatası: {save_error}")
                return jsonify({'success': False, 'error': f'Dosya kaydetme hatası: {save_error}'})
            
            # Resmi optimize et - Render.com için hızlı
            try:
                with Image.open(filepath) as img:
                    print(f"DEBUG UPLOAD: Orijinal boyut: {img.size}")
                    
                    # Render.com için agresif optimizasyon
                    if os.environ.get('RENDER'):
                        # Daha küçük boyut ve daha düşük kalite
                        max_size = (800, 600)  # Daha küçük
                        quality = 70  # Daha düşük kalite
                    else:
                        max_size = (1200, 800)
                        quality = 85
                    
                    # Mode conversion
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = background
                    
                    # Resize
                    if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                        img.thumbnail(max_size, Image.Resampling.LANCZOS)
                        print(f"DEBUG UPLOAD: Boyut küçültüldü: {img.size}")
                    
                    # JPEG olarak kaydet
                    if not filename.lower().endswith('.jpg'):
                        filename = os.path.splitext(filename)[0] + '.jpg'
                        filepath = os.path.join(upload_folder, filename)
                    
                    img.save(filepath, 'JPEG', quality=quality, optimize=True)
                    print(f"DEBUG UPLOAD: JPEG olarak optimize edildi, kalite: {quality}")
                    
            except Exception as e:
                print(f"DEBUG UPLOAD: Resim optimize hatası: {e}")
                # Optimize başarısız olsa da devam et
            
            # Final kontroller
            if os.path.exists(filepath):
                final_size = os.path.getsize(filepath)
                print(f"DEBUG UPLOAD: Final dosya boyutu: {final_size} bytes")
                
                # Final klasör kontrolü
                final_folder_contents = os.listdir(upload_folder)
                print(f"DEBUG UPLOAD: Final klasör içeriği: {final_folder_contents}")
                
                # Render.com için ek kontrol - dosyayı hemen test et
                if os.environ.get('RENDER'):
                    try:
                        with open(filepath, 'rb') as test_file:
                            test_data = test_file.read(100)  # İlk 100 byte'ı oku
                            print(f"DEBUG UPLOAD: Dosya erişim testi başarılı: {len(test_data)} bytes okundu")
                    except Exception as read_error:
                        print(f"DEBUG UPLOAD: Dosya erişim testi başarısız: {read_error}")
                        return jsonify({'success': False, 'error': f'Dosya erişim sorunu: {read_error}'})
                
                return jsonify({
                    'success': True, 
                    'filename': filename,
                    'url': f'/static/images/{filename}',
                    'render_mode': bool(os.environ.get('RENDER')),
                    'file_size': final_size,
                    'upload_folder': upload_folder
                })
            else:
                print(f"DEBUG UPLOAD: Final dosya bulunamadı: {filepath}")
                return jsonify({'success': False, 'error': 'Dosya işleme sonrası bulunamadı'})
        
        return jsonify({'success': False, 'error': 'Geçersiz dosya türü'})
        
    except Exception as e:
        print(f"DEBUG UPLOAD: Exception: {str(e)}")
        import traceback
        print(f"DEBUG UPLOAD: Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/eski-resimleri-temizle', methods=['POST'])
def api_eski_resimleri_temizle():
    """API: Eski resimleri temizle"""
    try:
        data = request.get_json() or {}
        max_yas_saat = data.get('max_yas_saat', 24)  # Varsayılan 24 saat
        
        silinen_sayisi = yonetici.eski_resimleri_temizle(max_yas_saat)
        
        return jsonify({
            'success': True,
            'message': f'{silinen_sayisi} eski dosya temizlendi',
            'silinen_sayisi': silinen_sayisi
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/sunucu-bilgileri', methods=['GET'])
def api_sunucu_bilgileri():
    """API: Sunucu ve depolama bilgileri"""
    try:
        import shutil
        
        # Disk kullanımı
        total, used, free = shutil.disk_usage('.')
        
        # Image folder bilgileri
        image_count = 0
        total_image_size = 0
        
        if os.path.exists(yonetici.IMAGE_FOLDER):
            for dosya in os.listdir(yonetici.IMAGE_FOLDER):
                dosya_yolu = os.path.join(yonetici.IMAGE_FOLDER, dosya)
                if os.path.isfile(dosya_yolu):
                    image_count += 1
                    total_image_size += os.path.getsize(dosya_yolu)
        
        return jsonify({
            'disk_kullanimi': {
                'toplam_gb': round(total / (1024**3), 2),
                'kullanilan_gb': round(used / (1024**3), 2),
                'bos_gb': round(free / (1024**3), 2),
                'kullanim_yuzdesi': round((used / total) * 100, 1)
            },
            'resim_depolama': {
                'resim_sayisi': image_count,
                'toplam_boyut_mb': round(total_image_size / (1024**2), 2),
                'klasor_yolu': yonetici.IMAGE_FOLDER
            },
            'haber_istatistikleri': {
                'toplam_haber': len(yonetici.haberler),
                'yeni_haber': len([h for h in yonetici.haberler if h.get('durum') == 'Yeni'])
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    # Upload klasörünü oluştur
    os.makedirs('static/uploads', exist_ok=True)
    os.makedirs('static/images', exist_ok=True)
    
    print("İstanbul Son Dakika - Haber Yönetim Sistemi")
    print("=" * 50)
    
    # Ngrok tunnel'ını başlat
    public_url = setup_ngrok()
    
    try:
        # Flask uygulamasını başlat
        if public_url:
            print("Flask sunucusu ngrok ile başlatılıyor...")
        else:
            print("Flask sunucusu local modda başlatılıyor...")
            
        app.run(
            debug=False,  # Ngrok ile debug=False öneriliyor
            host='0.0.0.0', 
            port=5000,
            threaded=True,
            use_reloader=False  # Ngrok ile reloader kapalı
        )
        
    except KeyboardInterrupt:
        print("\nUygulama durduruldu.")
    except Exception as e:
        print(f"Sunucu hatası: {e}")
    finally:
        cleanup_ngrok()

import os

if _name_ == "_main_":
    os.makedirs('static/uploads', exist_ok=True)
    
    port = int(os.environ.get("PORT", 5000))  # Render'ın verdiği PORT'u al
    app.run(host="0.0.0.0", port=port)
