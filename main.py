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
from urllib.parse import urljoin, urlparse
import hashlib

app = Flask(__name__)
app.secret_key = 'haber_yonetim_secret_key_2024'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

# İzin verilen dosya türleri
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

class HaberYoneticisi:
    def __init__(self):
        self.load_config()
        self.haberler = []
        self.secili_haber = None
        self.link_haberleri = []  # Link'ten çekilen haberler için ayrı liste
        
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
            
            # Flask upload folder'ını da aynı klasöre ayarla
            app.config['UPLOAD_FOLDER'] = self.IMAGE_FOLDER
            
        except Exception as e:
            print(f"Config yükleme hatası: {e}")
            # Fallback değerler
            self.IMAGE_FOLDER = 'static/images'
            app.config['UPLOAD_FOLDER'] = self.IMAGE_FOLDER

    def link_haber_cek(self, url):
        """Verilen linkten haber içeriğini çek - Geliştirilmiş versiyon"""
        try:
            # URL doğrulama
            parsed_url = urlparse(url)
            if not parsed_url.scheme:
                url = 'https://' + url
            
            domain = parsed_url.netloc.lower()
            
            # Sondakika.com için özel işlem
            if 'sondakika.com' in domain:
                return self._sondakika_haber_cek(url)
            
            # Diğer siteler için genel işlem
            return self._genel_haber_cek(url)
            
        except Exception as e:
            return {'success': False, 'error': f'Haber çekme hatası: {str(e)}'}
    
    def _sondakika_haber_cek(self, url):
        """Sondakika.com için özel haber çekici"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Cache-Control': 'no-cache',
                'Connection': 'keep-alive',
                'DNT': '1'
            }
            
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            # Encoding düzelt
            if response.encoding in ['ISO-8859-1', None] or response.apparent_encoding:
                response.encoding = response.apparent_encoding or 'utf-8'
            
            soup = BeautifulSoup(response.text, 'html.parser', from_encoding='utf-8')
            
            # Başlık çekme - Sondakika.com için spesifik
            baslik = ""
            
            # Title tag'den al (en güvenilir)
            title_tag = soup.find('title')
            if title_tag:
                baslik = title_tag.get_text().strip()
                # Site adını temizle
                if ' - Son Dakika' in baslik:
                    baslik = baslik.split(' - Son Dakika')[0].strip()
                elif ' - ' in baslik:
                    baslik = baslik.split(' - ')[0].strip()
            
            # Eğer başlık yoksa h1 dene
            if not baslik or len(baslik) < 10:
                h1_tags = soup.find_all('h1')
                for h1 in h1_tags:
                    text = h1.get_text().strip()
                    if text and len(text) > 10:
                        baslik = text
                        break
            
            # Eğer hala başlık yoksa meta tag dene
            if not baslik or len(baslik) < 10:
                meta_title = soup.find('meta', {'property': 'og:title'})
                if meta_title and meta_title.get('content'):
                    baslik = meta_title.get('content').strip()
            
            # İçerik çekme - Basit metin yaklaşımı
            icerik = ""
            
            # Ana içerik div'lerini bul
            content_divs = soup.find_all('div', class_=True)
            for div in content_divs:
                div_classes = ' '.join(div.get('class', [])).lower()
                # İçerik olabilecek div'leri kontrol et
                if any(keyword in div_classes for keyword in ['content', 'detail', 'article', 'news', 'text']):
                    # Script, style, nav vb. kaldır
                    for unwanted in div.find_all(['script', 'style', 'nav', 'footer', 'header', 'aside']):
                        unwanted.decompose()
                    
                    text = div.get_text().strip()
                    if len(text) > len(icerik):
                        icerik = text
            
            # Eğer div'lerden içerik çekilemezse paragrafları kullan
            if not icerik or len(icerik) < 200:
                paragraflar = soup.find_all('p')
                icerik_parcalari = []
                
                for p in paragraflar:
                    text = p.get_text().strip()
                    # Kısa ve anlamsız metinleri atla
                    if (len(text) > 30 and 
                        not text.lower().startswith(('reklam', 'cookie', 'gdpr', 'paylaş', 'takip')) and
                        'son dakika' not in text.lower()[:20]):  # Başlangıçta "son dakika" varsa atla
                        icerik_parcalari.append(text)
                
                # İlk 20 paragrafı birleştir
                if icerik_parcalari:
                    icerik = '\n\n'.join(icerik_parcalari[:20])
            
            # Metni temizle
            if icerik:
                icerik = re.sub(r'[\r\n\t]+', ' ', icerik)
                icerik = re.sub(r'\s+', ' ', icerik)
                icerik = icerik.strip()
            
            # Açıklama meta tag'den al
            aciklama = ""
            meta_desc = soup.find('meta', {'name': 'description'})
            if meta_desc and meta_desc.get('content'):
                aciklama = meta_desc.get('content').strip()
            
            # Eğer açıklama yoksa içeriğin başını al
            if not aciklama and icerik:
                aciklama = icerik[:200] + "..." if len(icerik) > 200 else icerik
            
            # Resim URL'si
            resim_url = ""
            meta_image = soup.find('meta', {'property': 'og:image'})
            if meta_image and meta_image.get('content'):
                resim_url = urljoin(url, meta_image.get('content'))
            
            # URL'den başlık çıkar (fallback)
            if not baslik or baslik.lower() in ['başlık bulunamadı', 'haber']:
                if '/haber-' in url:
                    url_part = url.split('/haber-')[-1].split('/')[0]
                    # URL'deki kelimeleri düzenle
                    words = url_part.replace('-ci-', ' ').replace('-', ' ').split()
                    baslik = ' '.join(word.capitalize() for word in words if len(word) > 2)[:100]
            
            # Final kontrol
            if not baslik:
                baslik = "Sondakika.com Haberi"
            
            if not icerik or len(icerik) < 50:
                # Son çare: Sayfadaki tüm metni al
                body_text = soup.get_text()
                if body_text and len(body_text) > 200:
                    # Gereksiz kısımları kaldır
                    lines = body_text.split('\n')
                    cleaned_lines = []
                    for line in lines:
                        line = line.strip()
                        if (len(line) > 30 and 
                            not line.lower().startswith(('reklam', 'cookie', 'menü', 'ana sayfa'))):
                            cleaned_lines.append(line)
                    
                    if cleaned_lines:
                        icerik = '\n'.join(cleaned_lines[:15])
            
            if not icerik or len(icerik) < 50:
                return {'success': False, 'error': 'Bu Sondakika.com haberinden yeterli içerik çekilemedi'}
            
            # Sonuç hazırla
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
            
            haber_data = {
                'id': url_hash,
                'baslik': baslik[:200],
                'haber_metni': icerik[:5000],
                'description': aciklama[:500] if aciklama else icerik[:200] + "...",
                'kaynak': 'Link',
                'durum': 'Yeni',
                'url': url,
                'resim_url': resim_url,
                'tarih': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'kelime_sayisi': len(icerik.split()) if icerik else 0
            }
            
            return {'success': True, 'haber': haber_data}
            
        except Exception as e:
            return {'success': False, 'error': f'Sondakika.com işlem hatası: {str(e)}'}
    
    def _genel_haber_cek(self, url):
        """Diğer siteler için genel haber çekici"""
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
                'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Cache-Control': 'no-cache',
                'DNT': '1',
                'Connection': 'keep-alive'
            }
            
            response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
            response.raise_for_status()
            
            if response.encoding == 'ISO-8859-1' or response.apparent_encoding:
                response.encoding = response.apparent_encoding or 'utf-8'
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Başlık çekme
            baslik = ""
            baslik_selectors = [
                'h1',
                'meta[property="og:title"]',
                'meta[name="twitter:title"]',
                'title',
                '.entry-title',
                '.post-title',
                '.article-title',
                '.news-title'
            ]
            
            for selector in baslik_selectors:
                try:
                    element = soup.select_one(selector)
                    if element:
                        if element.name == 'meta':
                            baslik = element.get('content', '').strip()
                        elif element.name == 'title':
                            baslik_text = element.get_text().strip()
                            if ' - ' in baslik_text:
                                baslik = baslik_text.split(' - ')[0].strip()
                            else:
                                baslik = baslik_text
                        else:
                            baslik = element.get_text().strip()
                        
                        if baslik and len(baslik) > 10:
                            break
                except:
                    continue
            
            # İçerik çekme
            icerik = ""
            icerik_selectors = [
                'article',
                '.entry-content',
                '.post-content',
                '.article-content',
                '.news-content',
                '.content',
                '.story-body',
                '.article-body'
            ]
            
            for selector in icerik_selectors:
                try:
                    elements = soup.select(selector)
                    if elements:
                        en_uzun = ""
                        for elem in elements:
                            for script in elem(["script", "style", "nav", "footer", "header"]):
                                script.decompose()
                            
                            metin = elem.get_text().strip()
                            metin = re.sub(r'[\r\n\t]+', ' ', metin)
                            metin = re.sub(r'\s+', ' ', metin)
                            
                            if len(metin) > len(en_uzun):
                                en_uzun = metin
                        
                        if len(en_uzun) > 200:
                            icerik = en_uzun
                            break
                except:
                    continue
            
            # Paragraflardan içerik
            if not icerik or len(icerik) < 200:
                paragraflar = soup.select('p')
                icerik_parcalari = []
                
                for p in paragraflar:
                    metin = p.get_text().strip()
                    metin = re.sub(r'[\r\n\t]+', ' ', metin)
                    metin = re.sub(r'\s+', ' ', metin)
                    
                    if len(metin) > 50:
                        icerik_parcalari.append(metin)
                
                if icerik_parcalari:
                    icerik = '\n\n'.join(icerik_parcalari[:15])
            
            # Açıklama
            aciklama = ""
            meta_desc = soup.select_one('meta[name="description"]')
            if meta_desc:
                aciklama = meta_desc.get('content', '').strip()
            
            if not aciklama and icerik:
                aciklama = icerik[:200] + "..." if len(icerik) > 200 else icerik
            
            # Resim
            resim_url = ""
            meta_image = soup.select_one('meta[property="og:image"]')
            if meta_image:
                resim_url = urljoin(url, meta_image.get('content', ''))
            
            # Final kontrol
            if not baslik:
                baslik = "Haber Başlığı"
            
            if not icerik or len(icerik) < 50:
                return {'success': False, 'error': 'Bu siteden yeterli içerik çekilemedi'}
            
            url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
            
            haber_data = {
                'id': url_hash,
                'baslik': baslik[:200],
                'haber_metni': icerik[:5000],
                'description': aciklama[:500] if aciklama else icerik[:200] + "...",
                'kaynak': 'Link',
                'durum': 'Yeni',
                'url': url,
                'resim_url': resim_url,
                'tarih': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'kelime_sayisi': len(icerik.split()) if icerik else 0
            }
            
            return {'success': True, 'haber': haber_data}
            
        except Exception as e:
            return {'success': False, 'error': f'Genel haber çekme hatası: {str(e)}'}

    def url_cikart(self, metin):
        """Metinden URL'yi çıkart"""
        try:
            # URL pattern'i
            url_pattern = r'https?://[^\s<>"{}|\\^`\[\]]+'
            
            # URL'yi bul
            urls = re.findall(url_pattern, metin)
            
            if urls:
                # İlk bulduğu URL'yi döndür
                return urls[0].strip()
            
            return None
            
        except Exception as e:
            print(f"URL çıkarma hatası: {e}")
            return None

    def haber_guncelle(self, haber_id, yeni_baslik=None, yeni_aciklama=None):
        """Haber bilgilerini güncelle"""
        try:
            # Ana haber listesinde bul ve güncelle
            for haber in self.haberler:
                if haber.get('id') == haber_id:
                    if yeni_baslik:
                        haber['baslik'] = yeni_baslik
                    if yeni_aciklama:
                        haber['description'] = yeni_aciklama
                    return True
            
            # Link haberlerinde bul ve güncelle
            for haber in self.link_haberleri:
                if haber.get('id') == haber_id:
                    if yeni_baslik:
                        haber['baslik'] = yeni_baslik
                    if yeni_aciklama:
                        haber['description'] = yeni_aciklama
                    return True
            
            return False
            
        except Exception as e:
            print(f"Haber güncelleme hatası: {e}")
            return False
        """Haber bilgilerini güncelle"""
        try:
            # Ana haber listesinde bul ve güncelle
            for haber in self.haberler:
                if haber.get('id') == haber_id:
                    if yeni_baslik:
                        haber['baslik'] = yeni_baslik
                    if yeni_aciklama:
                        haber['description'] = yeni_aciklama
                    return True
            
            # Link haberlerinde bul ve güncelle
            for haber in self.link_haberleri:
                if haber.get('id') == haber_id:
                    if yeni_baslik:
                        haber['baslik'] = yeni_baslik
                    if yeni_aciklama:
                        haber['description'] = yeni_aciklama
                    return True
            
            return False
            
        except Exception as e:
            print(f"Haber güncelleme hatası: {e}")
            return False

    def resim_indir_ve_kaydet(self, resim_url, dosya_adi=None):
        """URL'den resmi indir ve kaydet"""
        try:
            if not resim_url:
                return None
                
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            response = requests.get(resim_url, headers=headers, timeout=30)
            response.raise_for_status()
            
            # Dosya uzantısını belirle
            content_type = response.headers.get('content-type', '')
            uzanti = '.jpg'  # default
            
            if 'png' in content_type:
                uzanti = '.png'
            elif 'gif' in content_type:
                uzanti = '.gif'
            elif 'webp' in content_type:
                uzanti = '.webp'
            
            # Dosya adı oluştur
            if not dosya_adi:
                dosya_adi = f"link_resim_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            
            dosya_adi = dosya_adi + uzanti
            dosya_yolu = os.path.join(self.IMAGE_FOLDER, dosya_adi)
            
            # Klasör oluştur
            os.makedirs(self.IMAGE_FOLDER, exist_ok=True)
            
            # Resmi kaydet
            with open(dosya_yolu, 'wb') as f:
                f.write(response.content)
            
            # Resmi optimize et
            try:
                with Image.open(dosya_yolu) as img:
                    # RGBA'yı RGB'ye çevir
                    if img.mode in ('RGBA', 'LA', 'P'):
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode == 'RGBA' else None)
                        img = background
                    
                    # Boyut kontrolü
                    max_size = (1200, 800)
                    if img.size[0] > max_size[0] or img.size[1] > max_size[1]:
                        img.thumbnail(max_size, Image.Resampling.LANCZOS)
                    
                    # JPEG olarak kaydet
                    final_dosya = os.path.splitext(dosya_adi)[0] + '.jpg'
                    final_yol = os.path.join(self.IMAGE_FOLDER, final_dosya)
                    
                    img.save(final_yol, 'JPEG', quality=85, optimize=True)
                    
                    # Orijinal dosyayı sil (eğer farklıysa)
                    if final_yol != dosya_yolu:
                        os.remove(dosya_yolu)
                    
                    return final_dosya
                    
            except Exception as e:
                print(f"Resim optimize hatası: {e}")
                return dosya_adi
                
        except Exception as e:
            print(f"Resim indirme hatası: {e}")
            return None
            
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
            
            # Link haberlerini de ekle
            self.haberler.extend(self.link_haberleri)
            
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
            - MAKSIMUM 4 adet SEO uyumlu etiket oluştur
            - Çarpıcı ve SEO uyumlu bir açıklama (meta description) yaz (150-160 karakter)

            SADECE JSON formatında döndür:
            {{
              "icerik": "HTML içeriği - h1 başlık, içindekiler, h2 alt başlıklar ve paragraflar",
              "etiketler": ["maksimum 4 adet SEO uyumlu etiket"],
              "aciklama": "150-160 karakter arası çarpıcı SEO açıklaması",
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
                'etiketler': ai_data.get('etiketler', [])[:4],  # Maksimum 4 etiket
                'aciklama': ai_data.get('aciklama', ''),
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
    elif filtre == "Link":
        haberler = [h for h in yonetici.haberler if h.get('kaynak') == 'Link']
    else:
        haberler = yonetici.haberler
    
    # İstatistikler
    toplam_haber = len(yonetici.haberler)
    yeni_haber = len([h for h in yonetici.haberler if h.get('durum') == 'Yeni'])
    link_haber = len([h for h in yonetici.haberler if h.get('kaynak') == 'Link'])
    
    return render_template('ana_sayfa.html', 
                         haberler=haberler, 
                         filtre=filtre,
                         toplam_haber=toplam_haber,
                         yeni_haber=yeni_haber,
                         link_haber=link_haber)

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
    
    # Resim dosyalarını listele - app.config['UPLOAD_FOLDER'] kullan
    resim_dosyalari = []
    if os.path.exists(app.config['UPLOAD_FOLDER']):
        formatlar = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']
        resim_dosyalari = [f for f in os.listdir(app.config['UPLOAD_FOLDER']) 
                          if os.path.splitext(f)[1].lower() in formatlar]
    
    return render_template('haber_detay.html', 
                         haber=haber, 
                         kategoriler=kategoriler,
                         resim_dosyalari=resim_dosyalari)

# ======= API ENDPOINT'LERİ =======

@app.route('/api/link-haber-cek', methods=['POST'])
def api_link_haber_cek():
    """API: Link'ten haber çek"""
    try:
        data = request.get_json()
        girdi = data.get('url', '').strip()
        
        if not girdi:
            return jsonify({'success': False, 'error': 'URL gerekli'})
        
        # Metinden URL çıkart
        url = yonetici.url_cikart(girdi)
        
        if not url:
            # Eğer URL çıkarılamadıysa, girdinin kendisinin URL olup olmadığını kontrol et
            if girdi.startswith('http'):
                url = girdi
            else:
                return jsonify({'success': False, 'error': 'Geçerli bir URL bulunamadı. URL https:// ile başlamalıdır.'})
        
        # Haberi çek
        sonuc = yonetici.link_haber_cek(url)
        
        if sonuc['success']:
            haber = sonuc['haber']
            
            # Resim varsa indir
            resim_dosyasi = None
            if haber.get('resim_url'):
                resim_dosyasi = yonetici.resim_indir_ve_kaydet(
                    haber['resim_url'], 
                    f"link_{haber['id']}"
                )
                if resim_dosyasi:
                    haber['resim_dosyasi'] = resim_dosyasi
            
            # Link haberlerine ekle
            # Aynı URL'den daha önce çekilmiş mi kontrol et
            mevcut_var = False
            for mevcut_haber in yonetici.link_haberleri:
                if mevcut_haber.get('url') == url:
                    # Güncelle
                    mevcut_haber.update(haber)
                    mevcut_var = True
                    break
            
            if not mevcut_var:
                yonetici.link_haberleri.append(haber)
            
            # Ana haber listesini güncelle
            yonetici.haberleri_yenile()
            
            return jsonify({
                'success': True,
                'haber': haber,
                'message': 'Haber başarıyla çekildi'
            })
        else:
            return jsonify(sonuc)
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

@app.route('/api/haber-guncelle', methods=['POST'])
def api_haber_guncelle():
    """API: Haber başlık ve açıklamasını güncelle"""
    try:
        data = request.get_json()
        haber_id = data.get('haber_id')
        yeni_baslik = data.get('baslik', '').strip()
        yeni_aciklama = data.get('aciklama', '').strip()
        
        if not haber_id:
            return jsonify({'success': False, 'error': 'Haber ID gerekli'})
        
        if not yeni_baslik:
            return jsonify({'success': False, 'error': 'Başlık boş olamaz'})
        
        # Haberi güncelle
        basarili = yonetici.haber_guncelle(haber_id, yeni_baslik, yeni_aciklama)
        
        if basarili:
            return jsonify({
                'success': True,
                'message': 'Haber başarıyla güncellendi',
                'baslik': yeni_baslik,
                'aciklama': yeni_aciklama
            })
        else:
            return jsonify({'success': False, 'error': 'Haber bulunamadı'})
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})

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
                'yeni_haber': yeni_sayisi,
                'link_haber': len([h for h in yonetici.haberler if h.get('kaynak') == 'Link'])
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
        
        # Resim yolunu belirle - app.config['UPLOAD_FOLDER'] kullan
        resim_yolu = os.path.join(app.config['UPLOAD_FOLDER'], resim_dosyasi)
        
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
    # Upload klasörünü oluştur - config yüklendikten sonra
    if hasattr(yonetici, 'IMAGE_FOLDER'):
        os.makedirs(yonetici.IMAGE_FOLDER, exist_ok=True)
    else:
        os.makedirs('static/images', exist_ok=True)
    
    # Debug mode'da çalıştır
    app.run(debug=True, host='0.0.0.0', port=5000)
