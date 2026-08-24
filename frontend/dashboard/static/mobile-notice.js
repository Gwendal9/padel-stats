// mobile-notice.js — bandeau "pense pour ordinateur" affiche sur petit ecran,
// fermable et memorise via localStorage. Charge sur toutes les pages.
(function () {
  var KEY = 'padel_mobile_notice_dismissed';
  var el = document.getElementById('mobile-notice');
  if (!el) return;
  try {
    if (localStorage.getItem(KEY) === '1') el.classList.add('mn-dismissed');
  } catch (_) {}
  var btn = document.getElementById('mobile-notice-close');
  if (btn) {
    btn.addEventListener('click', function () {
      el.classList.add('mn-dismissed');
      try { localStorage.setItem(KEY, '1'); } catch (_) {}
    });
  }
})();
