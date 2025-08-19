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

app = Flask(__name__)
app.secret_key = 'haber_yonetim_secret_key_2024'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'static/images'

# İzin verilen dosya türleri
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

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
            
            # Diğer ayarlar
            self.IMAGE_FOLDER = self.config['settings']['image_folder']
            self.ONCEKI_HABERLER_FILE = self.config['settings']['onceki_haberler_file']
            self.ONCEKI_GUNCEL_HABERLER_FILE = self.config['settings']['onceki_guncel_haberler_file']
            
        except Exception as e:
            print(f"Config yükleme hatası: {e}")
            
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
            # Kapak fotoğrafı yükle
            kapak_fotografi_id = None
            if resim_yolu and os.path.exists(resim_yolu):
                kapak_fotografi_id = self.wordpress_medya_yukle(resim_yolu)
            
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
            
            response = requests.post(f'{self.WORDPRESS_URL}/wp-json/wp/v2/posts', 
                                   auth=self.WP_AUTH, json=data)
            
            if response.status_code == 201:
                post_data = response.json()
                return {'success': True, 'link': post_data.get('link', 'Link alınamadı')}
            else:
                return {'success': False, 'error': f"HTTP {response.status_code}: {response.text}"}
                
        except Exception as e:
            return {'success': False, 'error': str(e)}
            
    def wordpress_medya_yukle(self, dosya_yolu):
        """WordPress'e medya yükle"""
        try:
            if not os.path.exists(dosya_yolu):
                return None
            
            mime_types = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', 
                         '.gif': 'image/gif', '.webp': 'image/webp', '.bmp': 'image/bmp'}
            
            dosya_adi = os.path.basename(dosya_yolu)
            uzanti = os.path.splitext(dosya_adi)[1].lower()
            content_type = mime_types.get(uzanti, 'image/jpeg')
            
            with open(dosya_yolu, 'rb') as f:
                headers = {
                    'Content-Type': content_type,
                    'Content-Disposition': f'attachment; filename="{dosya_adi}"'
                }
                
                response = requests.post(f'{self.WORDPRESS_URL}/wp-json/wp/v2/media', 
                                       auth=self.WP_AUTH, headers=headers, data=f.read())
                
                if response.status_code == 201:
                    return response.json().get('id')
            return None
            
        except:
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
        
        if not baslik or not icerik:
            return jsonify({'success': False, 'error': 'Başlık ve içerik gerekli'})
        
        # Fotoğraf kontrolü - ZORUNLU
        if not resim_dosyasi:
            return jsonify({'success': False, 'error': 'Kapak fotoğrafı seçmek zorunludur!'})
        
        # Resim yolunu belirle
        resim_yolu = os.path.join(yonetici.IMAGE_FOLDER, resim_dosyasi)
        
        if not os.path.exists(resim_yolu):
            return jsonify({'success': False, 'error': 'Seçilen fotoğraf bulunamadı!'})
        
        # Yayınla
        sonuc = yonetici.haberi_yayinla(baslik, icerik, etiketler, kategori_id, resim_yolu)
        return jsonify(sonuc)
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/static/images/<filename>')
def uploaded_file(filename):
    """Yüklenen resimleri serve et"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/fotograf-yukle', methods=['POST'])
def api_fotograf_yukle():
    """API: Fotoğraf yükle"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'Dosya seçilmedi'})
        
        file = request.files['file']
        
        if file.filename == '':
            return jsonify({'success': False, 'error': 'Dosya seçilmedi'})
        
        if file and allowed_file(file.filename):
            # Güvenli dosya adı oluştur
            filename = secure_filename(file.filename)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
            filename = timestamp + filename
            
            # Klasör oluştur
            os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            
            # Dosyayı kaydet
            filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(filepath)
            
            # Resmi optimize et
            try:
                with Image.open(filepath) as img:
                    # RGBA'yı RGB'ye çevir (JPEG için)
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = background
                    
                    # Boyutları kontrol et ve küçült
                    max_size = (1200, 800)
                    if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                        img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    
                    # JPEG olarak kaydet
                    if not filename.lower().endswith('.jpg'):
                        filename = os.path.splitext(filename)[0] + '.jpg'
                        filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    
                    img.save(filepath, 'JPEG', quality=85, optimize=True)
                    
            except Exception as e:
                print(f"Resim optimize hatası: {e}")
            
            return jsonify({
                'success': True, 
                'filename': filename,
                'url': f'/static/images/{filename}'
            })
        
        return jsonify({'success': False, 'error': 'Geçersiz dosya türü'})
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

if __name__ == '__main__':
    # Upload klasörünü oluştur
    os.makedirs('static/uploads', exist_ok=True)
    
    # Debug mode'da çalıştır
    app.run(debug=True, host='0.0.0.0', port=5000)