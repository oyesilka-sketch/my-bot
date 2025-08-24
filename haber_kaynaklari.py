import requests
from bs4 import BeautifulSoup
import json
import re
import time
from datetime import datetime, timedelta
import random
from urllib.parse import urljoin, urlparse
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib

class NewsScrapingError(Exception):
    pass

class MultiNewsSource:
    def __init__(self):
        self.user_agents = [
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/120.0',
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15'
        ]
        
        self.istanbul_keywords = [
            'istanbul', 'İstanbul', 'ISTANBUL', 'İSTANBUL',
            'beyoğlu', 'kadıköy', 'üsküdar', 'beşiktaş', 'şişli',
            'fatih', 'bakırköy', 'zeytinburnu', 'pendik', 'maltepe',
            'ataşehir', 'taksim', 'sultanahmet', 'galata', 'beylikdüzü',
            'avcılar', 'bahçelievler', 'bağcılar', 'esenler', 'gaziosmanpaşa',
            'eyüpsultan', 'kağıthane', 'sarıyer', 'başakşehir', 'büyükçekmece',
            'çekmeköy', 'kartal', 'küçükçekmece', 'sancaktepe', 'silivri',
            'sultanbeyli', 'tuzla', 'umraniye', 'arnavutköy', 'çatalca',
            'esenyurt', 'güngören', 'sultangazi'
        ]
        
        # Haber siteleri yapılandırması - GÜNCEL VE ÇALIŞAN URL'LER
        self.news_sources = {
            'sondakika': {
                'base_url': 'https://www.sondakika.com',
                'istanbul_url': 'https://www.sondakika.com/istanbul/',
                'guncel_url': 'https://www.sondakika.com/guncel/',
                'enabled': True
            },
            'sozcu': {
                'base_url': 'https://www.sozcu.com.tr',
                'gundem_url': 'https://www.sozcu.com.tr/kategori/gundem/',
                'anasayfa_url': 'https://www.sozcu.com.tr',
                'enabled': True
            },
            'hurriyet': {
                'base_url': 'https://www.hurriyet.com.tr',
                'gundem_url': 'https://www.hurriyet.com.tr/gundem/',
                'anasayfa_url': 'https://www.hurriyet.com.tr/',
                'enabled': True
            },
            'milliyet': {
                'base_url': 'https://www.milliyet.com.tr',
                'gundem_url': 'https://www.milliyet.com.tr/gundem/',
                'anasayfa_url': 'https://www.milliyet.com.tr/',
                'enabled': True
            },
            'cnnturk': {
                'base_url': 'https://www.cnnturk.com',
                'anasayfa_url': 'https://www.cnnturk.com/',
                'enabled': True
            },
            'ntv': {
                'base_url': 'https://www.ntv.com.tr',
                'anasayfa_url': 'https://www.ntv.com.tr/',
                'enabled': False  # Timeout problemleri var
            },
            'haberturk': {
                'base_url': 'https://www.haberturk.com',
                'gundem_url': 'https://www.haberturk.com/gundem',
                'anasayfa_url': 'https://www.haberturk.com/',
                'enabled': True
            },
            'cumhuriyet': {
                'base_url': 'https://www.cumhuriyet.com.tr',
                'anasayfa_url': 'https://www.cumhuriyet.com.tr/',
                'enabled': True
            }
        }

    def get_random_headers(self):
        """Rastgele User-Agent ve headers döndürür"""
        return {
            'User-Agent': random.choice(self.user_agents),
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'tr-TR,tr;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Cache-Control': 'max-age=0',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none'
        }

    def safe_get(self, url, timeout=15, retries=2):
        """Güvenli HTTP GET isteği - Hızlı ayarlar"""
        for attempt in range(retries):
            try:
                session = requests.Session()
                session.headers.update(self.get_random_headers())
                
                response = session.get(url, timeout=timeout, allow_redirects=True)
                response.raise_for_status()
                
                if response.status_code == 200:
                    return response
                    
            except requests.exceptions.RequestException as e:
                print(f"❌ {url[:35]}... (Deneme {attempt + 1}/{retries}): {str(e)[:50]}...")
                if attempt < retries - 1:
                    time.sleep(1)  # Kısa bekleme
                    
        return None

    def clean_text(self, text):
        """Metni temizle ve normalize et"""
        if not text:
            return ""
        
        # HTML entities decode
        text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
        text = text.replace('&quot;', '"').replace('&#39;', "'")
        text = text.replace('&nbsp;', ' ').replace('&ndash;', '-').replace('&mdash;', '—')
        
        # Fazla boşlukları temizle
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'\n+', '\n', text)
        
        return text.strip()

    def clean_title(self, title):
        """Başlığı temizle"""
        if not title:
            return ""
        
        # Gereksiz kelimeler
        unwanted = ['Son Dakika:', 'CANLI:', 'VIDEO:', 'FOTO:', 'GALERİ:', 'ÖZEL:', 'HABER:', 'SON DAKİKA:']
        
        for word in unwanted:
            title = title.replace(word, '').strip()
        
        return self.clean_text(title)

    def format_date(self, date_str):
        """Tarih string'ini standart formata çevir"""
        if not date_str:
            return datetime.now().strftime('%d.%m.%Y %H:%M')
        
        try:
            # Türkçe ayları değiştir
            turkish_months = {
                'Ocak': '01', 'Şubat': '02', 'Mart': '03', 'Nisan': '04',
                'Mayıs': '05', 'Haziran': '06', 'Temmuz': '07', 'Ağustos': '08',
                'Eylül': '09', 'Ekim': '10', 'Kasım': '11', 'Aralık': '12'
            }
            
            for tr_month, num_month in turkish_months.items():
                date_str = date_str.replace(tr_month, num_month)
            
            # Farklı tarih formatlarını dene
            formats = [
                '%d.%m.%Y %H:%M',
                '%d/%m/%Y %H:%M',
                '%Y-%m-%d %H:%M:%S',
                '%d %m %Y %H:%M',
                '%d.%m.%Y',
                '%d/%m/%Y',
                '%Y-%m-%d'
            ]
            
            for fmt in formats:
                try:
                    dt = datetime.strptime(date_str.strip(), fmt)
                    return dt.strftime('%d.%m.%Y %H:%M')
                except:
                    continue
                    
            return self.clean_text(date_str)
            
        except:
            return datetime.now().strftime('%d.%m.%Y %H:%M')

    def is_istanbul_related(self, title, description="", content=""):
        """Haberin İstanbul ile ilgili olup olmadığını kontrol et"""
        text_to_check = f"{title} {description} {content}".lower()
        
        return any(keyword.lower() in text_to_check for keyword in self.istanbul_keywords)

    def is_today_news(self, date_str, hours_back=24):
        """Haberin bugün veya son X saat içinde olup olmadığını kontrol et"""
        try:
            if not date_str:
                return True  # Tarih yoksa kabul et
                
            # Farklı formatları dene
            for fmt in ['%d.%m.%Y %H:%M', '%d.%m.%Y', '%Y-%m-%d %H:%M:%S']:
                try:
                    news_date = datetime.strptime(date_str, fmt)
                    time_diff = datetime.now() - news_date
                    return time_diff <= timedelta(hours=hours_back)
                except:
                    continue
            
            return True  # Parse edilemezse kabul et
            
        except:
            return True

    def generate_news_id(self, title, url):
        """Haber için unique ID oluştur"""
        content = f"{title}{url}".encode('utf-8')
        return hashlib.md5(content).hexdigest()

    def scrape_sondakika(self, urls):
        """Sondakika.com'dan haber çek"""
        print("📰 Sondakika.com çekiliyor...")
        haberler = []
        
        for url in urls:
            try:
                response = self.safe_get(url)
                if not response:
                    continue
                    
                soup = BeautifulSoup(response.text, 'html.parser')
                haber_listesi = soup.select('li.nws')
                
                for haber in haber_listesi[:30]:  # İlk 30 haber
                    try:
                        # Başlık
                        baslik_elem = haber.select_one('span.title')
                        if not baslik_elem:
                            continue
                        baslik = self.clean_title(baslik_elem.get_text())
                        
                        # Link
                        link_elem = haber.select_one('a.content')
                        if not link_elem or not link_elem.get('href'):
                            continue
                        link = "https://www.sondakika.com" + link_elem['href']
                        
                        # İstanbul kontrolü
                        aciklama_elem = haber.select_one('p.news-detail')
                        aciklama = self.clean_text(aciklama_elem.get_text()) if aciklama_elem else ""
                        
                        if not self.is_istanbul_related(baslik, aciklama):
                            continue
                        
                        # Tarih
                        tarih_elem = haber.select_one('span.mdate')
                        tarih = self.format_date(tarih_elem.get_text()) if tarih_elem else ""
                        
                        # Güncel haber kontrolü
                        if not self.is_today_news(tarih):
                            continue
                        
                        # Resim
                        resim_elem = haber.select_one('img')
                        resim = ""
                        if resim_elem:
                            resim = resim_elem.get('src') or resim_elem.get('data-originalm') or ""
                            if resim and resim.startswith('/'):
                                resim = "https://www.sondakika.com" + resim
                        
                        haber_data = {
                            'id': self.generate_news_id(baslik, link),
                            'baslik': baslik,
                            'link': link,
                            'aciklama': aciklama,
                            'tarih': tarih,
                            'resim': resim,
                            'kaynak': 'sondakika',
                            'site_url': 'www.sondakika.com',
                            'durum': 'yeni'
                        }
                        
                        haberler.append(haber_data)
                        
                    except Exception as e:
                        continue
                        
            except Exception as e:
                print(f"❌ Sondakika hata: {str(e)[:50]}...")
        
        print(f"✅ Sondakika: {len(haberler)} haber")
        return haberler

    def scrape_generic_site(self, site_name, urls):
        """Genel site scraper"""
        print(f"📰 {site_name.title()} çekiliyor...")
        haberler = []
        
        for url in urls:
            try:
                response = self.safe_get(url)
                if not response:
                    continue
                    
                soup = BeautifulSoup(response.text, 'html.parser')
                
                # Genel selector'lar
                selectors = [
                    'article', 'div.news-item', 'div.card', 'li.news',
                    '.news-card', '.story', '.post', '.news-list-item',
                    'div[class*="news"]', 'div[class*="haber"]', 'a[href*="/haber"]'
                ]
                
                haber_listesi = []
                for selector in selectors:
                    haber_listesi = soup.select(selector)
                    if len(haber_listesi) > 5:  # En az 5 element olsun
                        break
                
                base_url = self.news_sources[site_name]['base_url']
                
                for haber in haber_listesi[:30]:  # İlk 30 element
                    try:
                        if haber.name == 'a':
                            link_elem = haber
                            baslik = self.clean_title(haber.get_text())
                        else:
                            link_elem = haber.select_one('a')
                            if not link_elem:
                                continue
                            baslik_elem = haber.select_one('h1, h2, h3, h4, .title, .headline, .baslik')
                            baslik = self.clean_title(baslik_elem.get_text()) if baslik_elem else self.clean_title(link_elem.get_text())
                        
                        if not baslik or len(baslik) < 10:
                            continue
                        
                        link = link_elem.get('href', '')
                        if not link:
                            continue
                            
                        if link.startswith('/'):
                            link = base_url + link
                        elif not link.startswith('http'):
                            continue
                        
                        # İstanbul kontrolü
                        aciklama_elem = haber.select_one('.summary, .excerpt, .description, p, .spot')
                        aciklama = self.clean_text(aciklama_elem.get_text()) if aciklama_elem else ""
                        
                        if not self.is_istanbul_related(baslik, aciklama):
                            continue
                        
                        # Tarih
                        tarih_elem = haber.select_one('.date, .time, time, .tarih, .zaman')
                        tarih = self.format_date(tarih_elem.get_text()) if tarih_elem else ""
                        
                        if not self.is_today_news(tarih):
                            continue
                        
                        # Resim
                        resim_elem = haber.select_one('img')
                        resim = ""
                        if resim_elem:
                            resim = resim_elem.get('src') or resim_elem.get('data-src') or resim_elem.get('data-lazy-src') or ""
                            if resim and resim.startswith('/'):
                                resim = base_url + resim
                        
                        haber_data = {
                            'id': self.generate_news_id(baslik, link),
                            'baslik': baslik,
                            'link': link,
                            'aciklama': aciklama,
                            'tarih': tarih,
                            'resim': resim,
                            'kaynak': site_name,
                            'site_url': urlparse(base_url).netloc,
                            'durum': 'yeni'
                        }
                        
                        haberler.append(haber_data)
                        
                    except Exception as e:
                        continue
                        
            except Exception as e:
                print(f"❌ {site_name.title()} hata: {str(e)[:50]}...")
        
        print(f"✅ {site_name.title()}: {len(haberler)} haber")
        return haberler

    def scrape_sozcu(self, urls):
        """Sözcü'den haber çek"""
        return self.scrape_generic_site('sozcu', urls)

    def scrape_hurriyet(self, urls):
        """Hürriyet'ten haber çek"""
        return self.scrape_generic_site('hurriyet', urls)

    def scrape_single_source(self, source_name, source_config):
        """Tek bir haber kaynağından haber çek"""
        if not source_config.get('enabled', False):
            return []
        
        try:
            urls = []
            for key, value in source_config.items():
                if key.endswith('_url') and value:
                    urls.append(value)
            
            if not urls:
                return []
            
            # Hızlı rate limiting
            time.sleep(random.uniform(0.1, 0.5))
            
            # Özel scraper'lar
            if source_name == 'sondakika':
                return self.scrape_sondakika(urls)
            elif source_name == 'sozcu':
                return self.scrape_sozcu(urls)
            elif source_name == 'hurriyet':
                return self.scrape_hurriyet(urls)
            else:
                return self.scrape_generic_site(source_name, urls)
                
        except Exception as e:
            print(f"❌ {source_name} genel hatası: {str(e)[:50]}...")
            return []

    def remove_duplicates(self, haberler):
        """Dublicate haberleri kaldır"""
        seen_ids = set()
        seen_titles = set()
        unique_haberler = []
        
        for haber in haberler:
            haber_id = haber.get('id', '')
            baslik = haber.get('baslik', '').lower().strip()
            
            # ID veya benzer başlık kontrolü
            title_similarity = any(
                self.title_similarity(baslik, seen_title) > 0.8 
                for seen_title in seen_titles
            )
            
            if haber_id not in seen_ids and not title_similarity:
                seen_ids.add(haber_id)
                seen_titles.add(baslik)
                unique_haberler.append(haber)
        
        return unique_haberler

    def title_similarity(self, title1, title2):
        """İki başlık arasındaki benzerliği hesapla (basit)"""
        if not title1 or not title2:
            return 0
        
        words1 = set(title1.lower().split())
        words2 = set(title2.lower().split())
        
        if not words1 or not words2:
            return 0
        
        intersection = words1.intersection(words2)
        union = words1.union(words2)
        
        return len(intersection) / len(union) if union else 0

    def sort_news_by_priority(self, haberler):
        """Haberleri önceliklerine göre sırala"""
        def priority_score(haber):
            score = 0
            
            # Kaynak önceliği
            source_priority = {
                'sondakika': 10, 'sozcu': 9, 'hurriyet': 8, 'milliyet': 7,
                'cnnturk': 6, 'ntv': 5, 'haberturk': 4, 'cumhuriyet': 3
            }
            score += source_priority.get(haber.get('kaynak', ''), 1)
            
            # Tarih önceliği (yeni haberler)
            try:
                tarih_str = haber.get('tarih', '')
                if tarih_str:
                    tarih = datetime.strptime(tarih_str, '%d.%m.%Y %H:%M')
                    hours_ago = (datetime.now() - tarih).total_seconds() / 3600
                    score += max(0, 10 - hours_ago)  # Son 10 saat içindeki haberler
            except:
                pass
            
            # İçerik kalitesi
            baslik_len = len(haber.get('baslik', ''))
            aciklama_len = len(haber.get('aciklama', ''))
            
            if baslik_len > 20:
                score += 2
            if aciklama_len > 50:
                score += 3
            if haber.get('resim'):
                score += 1
            
            return score
        
        return sorted(haberler, key=priority_score, reverse=True)

    def scrape_all_sources(self, max_workers=3):
        """Tüm haber kaynaklarından haberleri çek - Konservatif ayarlar"""
        print("🌍 Tüm haber kaynakları çekiliyor...")
        start_time = time.time()
        
        all_haberler = []
        
        # Önce aktif siteleri kontrol et
        active_sources = [name for name, config in self.news_sources.items() 
                         if config.get('enabled', False)]
        print(f"📊 Aktif siteler: {', '.join(active_sources)}")
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_source = {
                executor.submit(self.scrape_single_source, name, config): name
                for name, config in self.news_sources.items()
                if config.get('enabled', False)
            }
            
            for future in as_completed(future_to_source):
                source_name = future_to_source[future]
                try:
                    haberler = future.result(timeout=45)  # Thread timeout
                    if haberler:
                        all_haberler.extend(haberler)
                        print(f"✅ {source_name}: {len(haberler)} haber eklendi")
                    else:
                        print(f"⚠️ {source_name}: Haber bulunamadı")
                except Exception as e:
                    print(f"❌ {source_name} thread hatası: {str(e)[:50]}...")
        
        # Dublicate'leri kaldır
        original_count = len(all_haberler)
        all_haberler = self.remove_duplicates(all_haberler)
        removed_count = original_count - len(all_haberler)
        
        # Öncelikle sırala
        all_haberler = self.sort_news_by_priority(all_haberler)
        
        elapsed_time = time.time() - start_time
        print(f"\n📊 SONUÇ ÖZET:")
        print(f"⏱️  Süre: {elapsed_time:.1f} saniye")
        print(f"🔄 {removed_count} dublicate kaldırıldı")
        print(f"🎯 Toplam: {len(all_haberler)} benzersiz İstanbul haberi")
        
        # Kaynak dağılımı
        source_count = {}
        for haber in all_haberler:
            kaynak = haber.get('kaynak', 'bilinmiyor')
            source_count[kaynak] = source_count.get(kaynak, 0) + 1
        
        if source_count:
            print(f"📰 Kaynak Dağılımı:")
            for kaynak, count in sorted(source_count.items(), key=lambda x: x[1], reverse=True):
                print(f"   • {kaynak}: {count} haber")
        
        return all_haberler

    def get_fresh_istanbul_news(self, hours_back=6, max_news=100):
        """Son X saat içindeki taze İstanbul haberlerini getir"""
        print(f"🔥 Son {hours_back} saat içindeki taze İstanbul haberleri alınıyor...")
        
        all_news = self.scrape_all_sources()
        
        # Tarih filtresi uygula
        fresh_news = []
        for news in all_news:
            if self.is_today_news(news.get('tarih', ''), hours_back):
                fresh_news.append(news)
        
        # Limitle
        fresh_news = fresh_news[:max_news]
        
        print(f"✅ {len(fresh_news)} taze haber bulundu")
        return fresh_news

# ESKİ SİSTEMLE UYUMLULUK FONKSİYONLARI
def sirali_haber_kontrol(config, onceki_istanbul_haberler=None, onceki_guncel_haberler=None):
    """Eski sistemle uyumlu sıralı haber kontrolü"""
    print("🔍 Sıralı haber kontrolü başlıyor (Yeni Çoklu Site Sistemi)...")
    
    scraper = MultiNewsSource()
    
    # Taze İstanbul haberlerini çek
    yeni_haberler = scraper.get_fresh_istanbul_news(
        hours_back=config.get('settings', {}).get('hours_back', 12),
        max_news=config.get('settings', {}).get('max_news', 100)
    )
    
    # Önceki haberlerin linklerini çıkar
    onceki_linkler = set()
    if onceki_istanbul_haberler:
        onceki_linkler.update(h.get('link', '') for h in onceki_istanbul_haberler)
    if onceki_guncel_haberler:
        onceki_linkler.update(h.get('link', '') for h in onceki_guncel_haberler)
    
    # Yeni haberleri tespit et
    gercekten_yeni = []
    for haber in yeni_haberler:
        if haber.get('link') and haber['link'] not in onceki_linkler:
            haber['durum'] = 'yeni'
            gercekten_yeni.append(haber)
        else:
            haber['durum'] = 'eski'
    
    # Sonuçları logla
    if gercekten_yeni:
        print(f"🔥 {len(gercekten_yeni)} yeni haber bulundu!")
        # Tüm İstanbul haberlerini güncelle
        for haber in yeni_haberler:
            if haber not in gercekten_yeni:
                haber['durum'] = 'eski'
        return yeni_haberler, [], gercekten_yeni, 'multi'
    else:
        print("📰 Yeni haber bulunamadı")
        return yeni_haberler, [], [], 'none'

def tum_haberler_cek(config):
    """Eski sistemle uyumlu tüm haber çekme"""
    print("🌍 Tüm haber kaynakları çekiliyor (Yeni Çoklu Site Sistemi)...")
    
    scraper = MultiNewsSource()
    
    # Ayarları config'den al
    hours_back = config.get('settings', {}).get('hours_back', 12)
    max_news = config.get('settings', {}).get('max_news', 100)
    
    # Tüm haberleri çek
    haberler = scraper.get_fresh_istanbul_news(hours_back=hours_back, max_news=max_news)
    
    print(f"🎯 Toplam {len(haberler)} İstanbul haberi çekildi")
    return haberler

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

def safe_get(url, timeout=30, retries=3):
    """Eski sistemle uyumluluk için safe_get fonksiyonu"""
    scraper = MultiNewsSource()
    return scraper.safe_get(url, timeout, retries)

def metin_temizle(metin):
    """Eski sistemle uyumluluk için metin temizleme"""
    scraper = MultiNewsSource()
    return scraper.clean_text(metin)

def baslik_temizle(baslik):
    """Eski sistemle uyumluluk için başlık temizleme"""
    scraper = MultiNewsSource()
    return scraper.clean_title(baslik)

def tarih_formatla(tarih_str):
    """Eski sistemle uyumluluk için tarih formatlama"""
    scraper = MultiNewsSource()
    return scraper.format_date(tarih_str)

# Test ve kullanım
def test_multi_scraper():
    """Test fonksiyonu"""
    print("🧪 Çoklu haber kaynağı testi başlıyor...")
    
    scraper = MultiNewsSource()
    
    # Taze İstanbul haberlerini çek
    taze_haberler = scraper.get_fresh_istanbul_news(hours_back=12, max_news=50)
    
    if taze_haberler:
        print(f"\n📰 İlk 5 haber örneği:")
        for i, haber in enumerate(taze_haberler[:5], 1):
            print(f"\n{i}. {haber.get('baslik', 'N/A')[:80]}...")
            print(f"   Kaynak: {haber.get('kaynak', 'N/A')} | Tarih: {haber.get('tarih', 'N/A')}")
            print(f"   Link: {haber.get('link', 'N/A')[:80]}...")
    
    return taze_haberler

def hizli_test():
    """Hızlı test - sadece çalışan siteleri dene"""
    print("⚡ HIZLI TEST BAŞLIYOR...")
    print("="*40)
    
    scraper = MultiNewsSource()
    
    # Sadece güvenilir siteleri etkinleştir
    scraper.news_sources['ntv']['enabled'] = False
    scraper.news_sources['cumhuriyet']['enabled'] = False
    
    print("🎯 Test edilecek siteler: Sondakika, Sözcü, Hürriyet, Milliyet, CNN Türk, Habertürk")
    
    try:
        # Hızlı test - 3 saat içindeki haberler
        haberler = scraper.get_fresh_istanbul_news(hours_back=3, max_news=30)
        
        if haberler:
            print(f"\n✅ {len(haberler)} İstanbul haberi bulundu!")
            print(f"\n📰 İlk 3 haber:")
            for i, haber in enumerate(haberler[:3], 1):
                print(f"\n{i}. {haber.get('baslik', 'N/A')[:60]}...")
                print(f"   Kaynak: {haber.get('kaynak', 'N/A')} | {haber.get('tarih', 'N/A')}")
        else:
            print("❌ Hiç haber bulunamadı!")
            
    except Exception as e:
        print(f"❌ Test hatası: {e}")
    
    print("\n⚡ Hızlı test tamamlandı!")

def test_eski_sistem_uyumlulugi():
    """Eski sistem uyumluluğunu test et"""
    print("🔄 ESKİ SİSTEM UYUMLULUĞU TEST EDİLİYOR...")
    
    # Örnek config
    config = {
        'settings': {
            'sondakika_url': 'https://www.sondakika.com/istanbul/',
            'hours_back': 6,
            'max_news': 30  # Test için az haber
        }
    }
    
    try:
        print("\n1️⃣ tum_haberler_cek test ediliyor...")
        haberler = tum_haberler_cek(config)
        print(f"✅ {len(haberler)} haber çekildi")
        
        print("\n2️⃣ haber_istatistikleri test ediliyor...")
        stats = haber_istatistikleri(haberler)
        print(f"✅ İstatistikler: Toplam {stats['toplam']}, Yeni {stats['yeni']}")
        
        print("\n3️⃣ sirali_haber_kontrol test ediliyor...")
        istanbul_h, guncel_h, yeni_h, durum = sirali_haber_kontrol(config, [], [])
        print(f"✅ Kontrol sonucu: {len(yeni_h)} yeni haber, durum: {durum}")
        
        print("\n✅ Eski sistem uyumluluğu BAŞARILI!")
        return haberler
        
    except Exception as e:
        print(f"❌ Eski sistem test hatası: {e}")
        return []

# Ana config için güncelleme fonksiyonu
def get_updated_config():
    """Güncellenmiş config döndür"""
    return {
        'settings': {
            'hours_back': 12,  # Son 12 saat
            'max_news': 100,   # Maksimum haber sayısı
            'max_workers': 3,  # Thread sayısı
            'update_interval': 300,  # 5 dakika
            'enabled_sources': [
                'sondakika', 'sozcu', 'hurriyet', 'milliyet', 
                'cnnturk', 'haberturk', 'cumhuriyet'
            ]
        },
        'keywords': {
            'istanbul_filter': True,
            'today_filter': True,
            'duplicate_removal': True
        }
    }

if __name__ == "__main__":
    # Hızlı test modu
    print("🚀 Çoklu Site İstanbul Haber Çekici v2.0")
    print("="*50)
    
    try:
        # 1. Hızlı test (önerilen)
        print("\n🔥 HIZLI TEST (önerilen):")
        hizli_test()
        
        # 2. Eski sistem uyumluluk testi
        print("\n" + "="*50)
        print("🔄 ESKİ SİSTEM UYUMLULUK TESTİ:")
        test_eski_sistem_uyumlulugi()
        
    except KeyboardInterrupt:
        print("\n⏹️ Kullanıcı tarafından durduruldu")
    except Exception as e:
        print(f"\n❌ Beklenmeyen hata: {e}")
        
    print("\n✅ Tüm testler tamamlandı!")
    print("\n💡 Kullanım önerisi:")
    print("   • Normal kullanım için: scraper.get_fresh_istanbul_news()")
    print("   • Eski kod uyumluluğu için: tum_haberler_cek(config)")
