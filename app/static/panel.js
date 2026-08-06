// Panelin tek JS dosyası: tema düğmesi ve mobil çekmece.
// Sayfa geçişi JS'e bağlı DEĞİL — panel sunucu taraflı ve çok sayfalı.
// Mockup'taki SPA görünüm anahtarlama kodu bilerek alınmadı; JS kapalıyken
// ya da bozulduğunda paneli kullanılamaz hale getirirdi.

(function () {
  // ---- tema (tercih localStorage'da kalır) ----
  var rootEl = document.documentElement;
  var tgBtns = [].slice.call(document.querySelectorAll('.theme-toggle'));
  var MOON = '<svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M20 14.5A8 8 0 1 1 9.5 4a6.5 6.5 0 0 0 10.5 10.5Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>';
  var SUN = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><circle cx="12" cy="12" r="4" stroke="currentColor" stroke-width="1.6"/><path d="M12 2v2M12 20v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M2 12h2M20 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';

  function syncIcon() {
    var ic = rootEl.dataset.theme === 'dark' ? SUN : MOON;
    tgBtns.forEach(function (b) { b.innerHTML = ic; });
  }
  function setTheme(t) {
    rootEl.dataset.theme = t;
    try { localStorage.setItem('klinik-tema', t); } catch (e) { /* özel sekme */ }
    syncIcon();
  }
  var kayitli = null;
  try { kayitli = localStorage.getItem('klinik-tema'); } catch (e) { /* özel sekme */ }
  setTheme(kayitli || 'dark');
  tgBtns.forEach(function (b) {
    b.addEventListener('click', function () {
      setTheme(rootEl.dataset.theme === 'dark' ? 'light' : 'dark');
    });
  });

  // ---- mobil çekmece ----
  var sidebar = document.querySelector('.sidebar');
  var scrim = document.getElementById('scrim');
  var ham = document.getElementById('ham');
  if (!sidebar || !scrim || !ham) return;   // giriş sayfasında kenar çubuğu yok

  var MENUI = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M4 7h16M4 12h16M4 17h16" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';
  var CLOSEI = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>';

  function cekmece(ac) {
    sidebar.classList.toggle('open', ac);
    scrim.classList.toggle('show', ac);
    ham.innerHTML = ac ? CLOSEI : MENUI;
  }
  ham.addEventListener('click', function () {
    cekmece(!sidebar.classList.contains('open'));
  });
  scrim.addEventListener('click', function () { cekmece(false); });
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape') cekmece(false);
  });
})();
