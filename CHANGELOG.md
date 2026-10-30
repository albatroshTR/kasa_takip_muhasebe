# Sürüm notları

- **1.2.0**: Personel, şifreli veri, internet engeli ve telif bilgisi.
  - **Personel bölümü** (yalnız yönetici). Personel kartları. Personel hesabı cari gibi çalışır:
    - hak ediş kasayı etkilemez;
    - ödeme, avans ve borç verme gider olarak kasadan çıkar;
    - geri ödeme gelir olarak kasaya girer;
    - devreden bakiye girilebilir;
    - doğrudan ödemede hak ediş de birlikte kaydedilir.
    Toplu Maaş ekranı ve Personel Hesap Dökümü raporu (PDF/Excel) var. Kasayı etkileyen personel kayıtları defterde
    görünür. Kullanıcı rolü bu kayıtları isimsiz görür ve değiştiremez.
  - **Şifreli veri**. Veritabanı `kasa.kdb` ve yedekler `.kdb` olarak, AES-256-GCM ile şifreli saklanır. Dosyalar
    programdaki bir kullanıcının şifresi ya da kurtarma anahtarı olmadan açılamaz.
  - **Geçiş**. Güncellemeden sonraki ilk yönetici girişinde eski şifresiz veritabanı ve yedekler şifrelenir;
    doğrulandıktan sonra şifresiz halleri silinir. Ardından yeni kurtarma anahtarı bir kez gösterilir; bu anahtarı
    mutlaka saklayın.
  - **İnternete çıkmaz**. Programın ağ işlevleri kapatıldı ve EXE'ye Qt ağ modülü alınmıyor. Kurulum, Windows
    Güvenlik Duvarı'na engelleme kuralı ekler.
  - **Telif bilgisi**: © 2026 Tahir Ekinalan · tahirekinalan.com.
- **1.1.4**: İlk kurulum düzeltmesi; programın kendisinde değişiklik yok. `calistir.bat` ve `exe_olustur.bat`
  artık bilgisayardaki uygun Python'u kendisi seçiyor (64-bit, 3.11 – 3.15, önce 3.13). Önceden ilk bulunan
  Python kullanılıyordu; bu 32-bit ise "No matching distribution found for PySide6" hatası çıkıyordu. Uygun
  Python yoksa bulunanları ve nedenini yazıyor. Yarım kalmış ya da uygun olmayan Python ile kurulmuş `.venv`
  kendiliğinden yenileniyor (önceden sonraki açılışlar sessizce başarısız oluyordu). Paket kurulumu başarısız
  olursa olası nedenler ve pip ayarları yazılıyor. Paket sürümlerine üst sınır eklendi.
- **1.1.3**: Derleme düzeltmeleri; programın kendisinde değişiklik yok. `exe_olustur.bat` Inno Setup 7'yi de
  buluyor (önceden yalnızca 6 aranıyordu) ve bitişte eski sürüm numaralı kurulum adı yazmıyor. VS Code yapay zekâ
  asistanı için `EXE_OLUSTURMA_PROMPT.md` eklendi. Testler Windows'ta geçici klasör temizliğinde takılmıyor; sürüm
  numarasının tüm dosyalarda aynı olduğunu denetleyen test eklendi.
- **1.1.2**: Dönem "Tüm Zamanlar" seçildiğinde raporların başlığında, devir satırında ve dosya adında
  01.01.2000 – 31.12.2099 yerine kayıtların gerçek aralığı yazılıyor ("tüm kayıtlar" notuyla); cari ekstre ekranı
  ve Hareketler Excel'i de aynı şekilde düzeltildi. Yardım ve kılavuza "Tüm defteri Excel'e almak" eklendi.
- **1.1.1**: Bağımsız kod incelemesi düzeltmeleri. Rapor önizlemesinden açılan kayıtta yeni cari eklenince
  yaşanan çökme giderildi; açılan pencereler kapanınca bellekten silinir (önceden gün boyu birikiyor ve kapanışta
  çökmeye yol açabiliyordu). Kapatılmadan kilitlenen günler artık "kapatıldı" sayılmıyor (ayrı listelenir); cari
  açılış bakiyeleri dönem veresiyesi sayılmıyor; önceki yılın verisi eksikse yıllık değişim gösterilmiyor; Trend
  karşılaştırma çizgisi tarihe göre hizalanıyor; Excel'de kullanıcı metinleri formül olarak yazılmıyor; uzun
  raporların PDF'i ~5 kat hızlandı; TOPLAM satırları sıralamada en altta kalıyor; kullanıcı rolü yönetim raporlarını
  "Son Oluşturulanlar"da görmüyor.
- **1.1.0**: Analiz ekranı 7 sekmeli gösterge paneline dönüştü (göstergeler, ibreli göstergeler, çizgi / halka /
  yığılmış grafikler, takvim ısı haritası, yaşlandırma, mizan, yıllık karşılaştırma, otomatik yorumlar).
  Yeni **Raporlar** bölümü: 13 rapor, canlı önizleme, PDF ve Excel. Deneme verisi ~14 aylık oldu.
- **1.0.0**: İlk sürüm.
