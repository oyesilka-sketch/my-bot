import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime
import random
from urllib.parse import urljoin, urlparse

def safe_get(url, timeout=30, retries=3):
    """Güvenli HTTP GET isteği"""
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }
    
    for deneme in range(retries):
        try:
            session = requests.Session()
            session.headers.update(headers)
            
            response = session.get(url, timeout=timeout, allow_redirects=True)
            response.raise_for_status()
            
            if response.status_code == 200:
                return response
                
        except requests.exceptions.RequestException as e:
            print(f"❌ İstek hatası (Deneme {deneme + 1}/{retries}): {e}")
            if deneme < retries - 1:
                time.sleep(2 ** deneme)  # Exponential backoff
            
    return None

def metin_temizle(metin):
    """Metni temizle ve normalize et"""
    if not metin:
        return ""
    
    # HTML entities decode
    metin = metin.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    metin = metin.replace('&quot;', '"').replace('&#39;', "'")
    metin = metin.replace('&nbsp;', ' ')
    
    # Fazla boşlukları temizle
    metin = re.sub(r'\s+', ' ', metin)
    metin = re.sub(r'\n+', '\n', metin)
    
    return metin.strip()

def baslik_temizle(baslik):
    """Başlığı temizle"""
    if not baslik:
        return ""
    
    # Gereksiz kelimeler
    gereksiz = ['Son Dakika:', 'CANLI:', 'VIDEO:', 'FOTO:', 'GALERİ:', 'ÖZEL:']
    
    for kelime in gereksiz:
        baslik = baslik.replace(kelime, '').strip()
    
    return metin_temizle(baslik)

def tarih_formatla(tarih_str):
    """Tarih string'ini standart formata çevir"""
    if not tarih_str:
        return datetime.now().strftime('%d.%m.%Y %H:%M')
    
    try:
        # Farklı tarih formatlarını dene
        formatlar = [
            '%d.%m.%Y %H:%M',
            '%d/%m/%Y %H:%M',
            '%Y-%m-%d %H:%M:%S',
            '%d %B %Y %H:%M'
        ]
        
        for format_str in formatlar:
            try:
                dt = datetime.strptime(tarih_str, format_str)
                return dt.strftime('%d.%m.%Y %H:%M')
            except:
                continue
                
        # Eğer hiçbiri çalışmazsa, sadece temizle
        return metin_temizle(tarih_str)
        
    except:
        return datetime.now().strftime('%d.%m.%Y %H:%M')

def sondakika_haber_detay_cek(link):
    """Sondakika.com'dan haber detaylarını çeker - İyileştirilmiş versiyon"""
    try:
        print(f"📄 Detay çekiliyor: {link[:50]}...")
        
        response = safe_get(link)
        if not response:
            print("❌ Sayfaya erişilemedi")
            return '', '', ''
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        headline = description = haber_metni = ''
        
        # JSON-LD script'ini bul (öncelikli)
        json_script = soup.find('script', {'type': 'application/ld+json'})
        if json_script:
            try:
                json_data = json.loads(json_script.string)
                
                if '@graph' in json_data:
                    for item in json_data['@graph']:
                        if item.get('@type') == 'NewsArticle':
                            headline = item.get('headline', '')
                            description = item.get('description', '')
                            break
                elif json_data.get('@type') == 'NewsArticle':
                    headline = json_data.get('headline', '')
                    description = json_data.get('description', '')
            except json.JSONDecodeError:
                print("⚠️ JSON-LD parse hatası")
        
        # Alternatif başlık kaynakları
        if not headline:
            title_tag = soup.find('title')
            if title_tag:
                headline = baslik_temizle(title_tag.get_text())
                
            # h1 başlık
            h1_tag = soup.find('h1')
            if h1_tag and not headline:
                headline = baslik_temizle(h1_tag.get_text())
        
        # Meta description
        if not description:
            meta_desc = soup.find('meta', {'name': 'description'})
            if meta_desc:
                description = metin_temizle(meta_desc.get('content', ''))
        
        # Haber metnini çek - çoklu selector
        haber_selectors = [
            'div.wrapper.detay-v3_3.haber_metni',
            'div.haber_metni',
            'div.news-content',
            'div.article-content',
            'div.content',
            'article'
        ]
        
        for selector in haber_selectors:
            try:
                haber_div = soup.select_one(selector)
                if haber_div:
                    # Gereksiz elementleri temizle
                    for unwanted in haber_div.find_all(['script', 'style', 'aside', 'nav', 'footer']):
                        unwanted.decompose()
                    
                    haber_metni = str(haber_div)
                    if len(haber_metni.strip()) > 100:  # Minimum içerik kontrolü
                        break
            except:
                continue
        
        # Son temizleme
        headline = baslik_temizle(headline)
        description = metin_temizle(description)
        
        print(f"✅ Detay çekildi: {len(headline)} char başlık, {len(haber_metni)} char içerik")
        return headline, description, haber_metni
        
    except Exception as e:
        print(f"❌ Detay çekme hatası: {e}")
        return '', '', ''

def sondakika_haberler_cek(url="https://www.sondakika.com/istanbul/"):
    """Sondakika.com'dan haberleri çeker - İyileştirilmiş versiyon"""
    print(f"📰 Sayfa çekiliyor: {url}")
    
    try:
        response = safe_get(url)
        if not response:
            print("❌ Ana sayfaya erişilemedi")
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        haberler = []
        
        # Çoklu haber listesi selector'ları
        haber_selectors = [
            'li.nws',
            'div.news-item',
            'article.news',
            'div.haber-item'
        ]
        
        haber_listesi = []
        for selector in haber_selectors:
            haber_listesi = soup.select(selector)
            if haber_listesi:
                print(f"✅ {len(haber_listesi)} haber bulundu ({selector})")
                break
        
        if not haber_listesi:
            print("❌ Haber listesi bulunamadı")
            return []
        
        for i, haber in enumerate(haber_listesi, 1):
            try:
                print(f"📰 {i}/{len(haber_listesi)}", end=" ", flush=True)
                
                # Başlık çekme - çoklu selector
                baslik_text = ""
                for baslik_sel in ['span.title', 'h2', 'h3', '.title', '.headline']:
                    baslik = haber.select_one(baslik_sel)
                    if baslik:
                        baslik_text = baslik_temizle(baslik.get_text())
                        break
                
                if not baslik_text:
                    print("❌ Başlık yok")
                    continue
                
                # Link çekme
                link_url = ""
                for link_sel in ['a.content', 'a', 'a.news-link']:
                    link = haber.select_one(link_sel)
                    if link and link.get('href'):
                        href = link['href']
                        if href.startswith('/'):
                            link_url = "https://www.sondakika.com" + href
                        elif href.startswith('http'):
                            link_url = href
                        break
                
                if not link_url:
                    print("❌ Link yok")
                    continue
                
                # Açıklama çekme
                aciklama_text = ""
                for desc_sel in ['p.news-detail', '.description', '.summary', '.excerpt']:
                    aciklama = haber.select_one(desc_sel)
                    if aciklama:
                        aciklama_text = metin_temizle(aciklama.get_text())
                        break
                
                # Zaman çekme
                zaman_text = ""
                for time_sel in ['span.mdate', '.date', '.time', 'time']:
                    zaman = haber.select_one(time_sel)
                    if zaman:
                        zaman_text = tarih_formatla(zaman.get_text())
                        break
                
                if not zaman_text:
                    zaman_text = datetime.now().strftime('%d.%m.%Y %H:%M')
                
                # Resim çekme
                resim_url = ""
                resim = haber.select_one('img')
                if resim:
                    resim_url = resim.get('src') or resim.get('data-src') or resim.get('data-originalm') or ""
                    if resim_url and resim_url.startswith('/'):
                        resim_url = "https://www.sondakika.com" + resim_url
                
                # Haber detayını çek (opsiyonel - performans için)
                headline, description, haber_metni = "", "", ""
                if len(haberler) < 20:  # İlk 20 haber için detay çek
                    headline, description, haber_metni = sondakika_haber_detay_cek(link_url)
                else:
                    # Detay çekmeden temel bilgileri kullan
                    headline = baslik_text
                    description = aciklama_text
                
                haber_data = {
                    'baslik': baslik_text,
                    'link': link_url,
                    'aciklama': aciklama_text,
                    'zaman': zaman_text,
                    'tarih': zaman_text,  # Uyumluluk için
                    'resim': resim_url,
                    'headline': headline or baslik_text,
                    'description': description or aciklama_text,
                    'haber_metni': haber_metni,
                    'kaynak': 'sondakika-istanbul',
                    'durum': 'yeni',  # GUI için
                    'id': f"sondakika_{i}_{int(time.time())}"  # Unique ID
                }
                
                haberler.append(haber_data)
                print("✅")
                
            except Exception as e:
                print(f"❌ Haber parse hatası: {e}")
                continue
        
        print(f"\n✅ Toplam {len(haberler)} haber çekildi")
        return haberler
        
    except Exception as e:
        print(f"❌ Sayfa çekme hatası: {e}")
        return []

def sondakika_guncel_haberler_cek(url="https://www.sondakika.com/guncel/"):
    """Sondakika.com/guncel sayfasından haberleri çeker - İyileştirilmiş versiyon"""
    print(f"📰 Güncel sayfa çekiliyor: {url}")
    
    try:
        response = safe_get(url)
        if not response:
            print("❌ Güncel sayfaya erişilemedi")
            return []
            
        soup = BeautifulSoup(response.text, 'html.parser')
        
        haberler = []
        haber_listesi = soup.select('li.nws')
        
        if not haber_listesi:
            print("❌ Güncel haber listesi bulunamadı")
            return []
        
        print(f"✅ {len(haber_listesi)} güncel haber bulundu")
        
        for i, haber in enumerate(haber_listesi, 1):
            try:
                print(f"📰 {i}/{len(haber_listesi)}", end=" ", flush=True)
                
                # Başlık
                baslik = haber.select_one('span.title')
                baslik_text = baslik_temizle(baslik.get_text()) if baslik else "Başlık yok"
                
                if not baslik_text or baslik_text == "Başlık yok":
                    print("❌ Başlık yok")
                    continue
                
                # Link
                link = haber.select_one('a.content')
                if not link or not link.get('href'):
                    print("❌ Link yok")
                    continue
                    
                link_url = "https://www.sondakika.com" + link['href']
                
                # Açıklama
                aciklama = haber.select_one('p.news-detail')
                aciklama_text = metin_temizle(aciklama.get_text()) if aciklama else ""
                
                # Zaman
                zaman = haber.select_one('span.mdate')
                zaman_text = tarih_formatla(zaman.get_text()) if zaman else datetime.now().strftime('%d.%m.%Y %H:%M')
                
                # Resim
                resim = haber.select_one('img')
                resim_url = ""
                if resim:
                    resim_url = resim.get('src') or resim.get('data-originalm') or ""
                    if resim_url and resim_url.startswith('/'):
                        resim_url = "https://www.sondakika.com" + resim_url
                
                # Detay çek (opsiyonel)
                headline, description, haber_metni = "", "", ""
                if len(haberler) < 15:  # İlk 15 haber için detay çek
                    headline, description, haber_metni = sondakika_haber_detay_cek(link_url)
                else:
                    headline = baslik_text
                    description = aciklama_text
                
                haber_data = {
                    'baslik': baslik_text,
                    'link': link_url,
                    'aciklama': aciklama_text,
                    'zaman': zaman_text,
                    'tarih': zaman_text,
                    'resim': resim_url,
                    'headline': headline or baslik_text,
                    'description': description or aciklama_text,
                    'haber_metni': haber_metni,
                    'kaynak': 'sondakika-guncel',
                    'durum': 'yeni',
                    'id': f"guncel_{i}_{int(time.time())}"
                }
                
                haberler.append(haber_data)
                print("✅")
                
            except Exception as e:
                print(f"❌ Güncel haber parse hatası: {e}")
                continue
        
        print(f"\n✅ Toplam {len(haberler)} güncel haber çekildi")
        return haberler
        
    except Exception as e:
        print(f"❌ Güncel sayfa çekme hatası: {e}")
        return []

def sirali_haber_kontrol(config, onceki_istanbul_haberler, onceki_guncel_haberler):
    """Sıralı haber kontrolü: İlk İstanbul, sonra Güncel - İyileştirilmiş"""
    
    print("🔍 Haber kontrolü başlıyor...")
    
    # 1. İstanbul sayfasını kontrol et
    print("\n📰 İstanbul sayfası kontrol ediliyor...")
    istanbul_haberler = sondakika_haberler_cek(config['settings']['sondakika_url'])
    print(f"✅ İstanbul: {len(istanbul_haberler)} haber çekildi")
    
    # Önceki İstanbul haberlerinin linklerini çıkar
    onceki_istanbul_linkler = {h.get('link', '') for h in onceki_istanbul_haberler if h.get('link')}
    
    # Yeni İstanbul haberlerini bul
    yeni_istanbul = []
    for haber in istanbul_haberler:
        if haber.get('link') and haber['link'] not in onceki_istanbul_linkler:
            haber['durum'] = 'yeni'
            yeni_istanbul.append(haber)
    
    if yeni_istanbul:
        print(f"🔥 İstanbul'da {len(yeni_istanbul)} yeni haber bulundu!")
        # Tüm İstanbul haberlerini güncelle
        for haber in istanbul_haberler:
            if haber not in yeni_istanbul:
                haber['durum'] = 'eski'
        return istanbul_haberler, onceki_guncel_haberler, yeni_istanbul, 'istanbul'
    
    # 2. İstanbul'da yeni haber yoksa Güncel sayfasını kontrol et
    print("\n📰 Güncel sayfası kontrol ediliyor...")
    guncel_haberler = sondakika_guncel_haberler_cek()
    print(f"✅ Güncel: {len(guncel_haberler)} haber çekildi")
    
    # Önceki Güncel haberlerinin linklerini çıkar
    onceki_guncel_linkler = {h.get('link', '') for h in onceki_guncel_haberler if h.get('link')}
    
    # Yeni Güncel haberlerini bul
    yeni_guncel = []
    for haber in guncel_haberler:
        if haber.get('link') and haber['link'] not in onceki_guncel_linkler:
            haber['durum'] = 'yeni'
            yeni_guncel.append(haber)
    
    if yeni_guncel:
        print(f"🔥 Güncel'de {len(yeni_guncel)} yeni haber bulundu!")
        # Tüm Güncel haberlerini güncelle
        for haber in guncel_haberler:
            if haber not in yeni_guncel:
                haber['durum'] = 'eski'
        return istanbul_haberler, guncel_haberler, yeni_guncel, 'guncel'
    
    # 3. Her iki sayfada da yeni haber yok
    print("📰 Hiçbir sayfada yeni haber yok")
    
    # Eski haberler olarak işaretle
    for haber in istanbul_haberler:
        haber['durum'] = 'eski'
    for haber in guncel_haberler:
        haber['durum'] = 'eski'
        
    return istanbul_haberler, guncel_haberler, [], 'none'

def tum_haberler_cek(config):
    """Tüm aktif haber kaynaklarından haberleri çeker - İyileştirilmiş"""
    print("🌍 Tüm haber kaynakları çekiliyor...")
    
    try:
        # İstanbul haberleri
        print("\n1️⃣ İstanbul haberleri çekiliyor...")
        istanbul_haberler = sondakika_haberler_cek(config['settings']['sondakika_url'])
        
        # Güncel haberler
        print("\n2️⃣ Güncel haberler çekiliyor...")
        guncel_haberler = sondakika_guncel_haberler_cek()
        
        # Birleştir
        tum_haberler = istanbul_haberler + guncel_haberler
        
        # İstatistikler
        print(f"\n📊 ÖZET:")
        print(f"✅ İstanbul: {len(istanbul_haberler)} haber")
        print(f"✅ Güncel: {len(guncel_haberler)} haber")
        print(f"🎯 Toplam: {len(tum_haberler)} haber çekildi")
        
        return tum_haberler
        
    except Exception as e:
        print(f"❌ Genel haber çekme hatası: {e}")
        return []

def haber_istatistikleri(haberler):
    """Haber listesi istatistiklerini döndür"""
    if not haberler:
        return {"toplam": 0, "yeni": 0, "eski": 0, "kaynak_dagilimi": {}}
    
    toplam = len(haberler)
    yeni = len([h for h in haberler if h.get('durum') == 'yeni'])
    eski = toplam - yeni
    
    # Kaynak dağılımı
    kaynak_dagilimi = {}
    for haber in haberler:
        kaynak = haber.get('kaynak', 'bilinmiyor')
        kaynak_dagilimi[kaynak] = kaynak_dagilimi.get(kaynak, 0) + 1
    
    return {
        "toplam": toplam,
        "yeni": yeni, 
        "eski": eski,
        "kaynak_dagilimi": kaynak_dagilimi
    }

# Test fonksiyonu
def test_haber_cekme():
    """Test amaçlı haber çekme"""
    print("🧪 Test başlıyor...")
    
    # Örnek config
    config = {
        'settings': {
            'sondakika_url': 'https://www.sondakika.com/istanbul/'
        }
    }
    
    # Test
    haberler = tum_haberler_cek(config)
    stats = haber_istatistikleri(haberler)
    
    print(f"\n📊 Test Sonuçları:")
    print(f"Toplam: {stats['toplam']}")
    print(f"Kaynak dağılımı: {stats['kaynak_dagilimi']}")
    
    if haberler:
        print(f"\n📰 İlk haber örneği:")
        ilk_haber = haberler[0]
        print(f"Başlık: {ilk_haber.get('baslik', 'N/A')[:100]}...")
        print(f"Link: {ilk_haber.get('link', 'N/A')}")
        print(f"Kaynak: {ilk_haber.get('kaynak', 'N/A')}")

if __name__ == "__main__":
    test_haber_cekme()
