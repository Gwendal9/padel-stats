// mobile-notice.js — couche mobile partagee (charge sur toutes les pages).
// Sous 768px : la barre laterale est masquee (mobile.css) et on injecte une
// barre de navigation en bas de l'ecran + un panneau "Plus".
// (Le fichier garde son ancien nom pour ne pas toucher aux 13 pages.)
(function () {
  if (!document.querySelector('aside.fixed')) return; // pages sans barre laterale (mentions, admin...)

  var p = (location.pathname.replace(/\/+$/, '') || '/');
  var is = function (re) { return re.test(p); };

  var I = {
    home: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="8"/><path d="M14.5 9.5 12 15 9.5 9.5 12 11.5z" stroke-linejoin="round"/></svg>',
    rank: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M8 21h8M12 17v4M7 4h10v4a5 5 0 0 1-10 0V4ZM5 4H3v3a3 3 0 0 0 3 3M19 4h2v3a3 3 0 0 1-3 3"/></svg>',
    map: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 21s-6-5.7-6-10a6 6 0 1 1 12 0c0 4.3-6 10-6 10Z"/><circle cx="12" cy="11" r="2"/></svg>',
    club: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 21V8l9-5 9 5v13M3 21h18M9 21v-6h6v6"/></svg>',
    more: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="5" cy="12" r="1.6"/><circle cx="12" cy="12" r="1.6"/><circle cx="19" cy="12" r="1.6"/></svg>'
  };

  var items = [
    { href: '/', label: 'Accueil', icon: I.home, on: is(/^\/$/) },
    { href: '/classement', label: 'Joueurs', icon: I.rank, on: is(/^\/classement/) || is(/^\/joueur/) },
    { href: '/carte', label: 'Carte', icon: I.map, on: is(/^\/carte/) },
    { href: '/clubs', label: 'Clubs', icon: I.club, on: is(/^\/clubs?(\/|$)/) }
  ];
  var moreOn = !items.some(function (i) { return i.on; });

  var nav = document.createElement('nav');
  nav.id = 'mnav';
  nav.setAttribute('aria-label', 'Navigation');
  nav.innerHTML = items.map(function (i) {
    return '<a href="' + i.href + '" class="' + (i.on ? 'on' : '') + '">' + i.icon + '<span>' + i.label + '</span></a>';
  }).join('') +
    '<button type="button" id="mnav-more" class="' + (moreOn ? 'on' : '') + '" aria-label="Plus">' + I.more + '<span>Plus</span></button>';

  var links = [
    ['/tournois', 'Tournois'],
    ['/graphe', 'Graphe des partenaires'],
    ['/joueur/random', 'Un joueur au hasard'],
    ['/club/random', 'Un club au hasard'],
    ['/tournoi/random', 'Un tournoi au hasard'],
    ['/suggestions', 'Suggestions'],
    ['https://gwendal9.github.io', 'Contact'],
    ['/mentions', 'Mentions légales']
  ];
  var sheet = document.createElement('div');
  sheet.id = 'msheet';
  sheet.innerHTML = '<div class="msheet-bg"></div><div class="msheet-panel" role="dialog" aria-label="Plus"><div class="msheet-grip"></div>' +
    links.map(function (l) {
      var ext = /^https?:/.test(l[0]) ? ' target="_blank" rel="noopener"' : '';
      return '<a href="' + l[0] + '"' + ext + '>' + l[1] + '</a>';
    }).join('') + '</div>';

  function open() { sheet.classList.add('open'); }
  function close() { sheet.classList.remove('open'); }

  function mount() {
    document.body.appendChild(nav);
    document.body.appendChild(sheet);
    document.getElementById('mnav-more').addEventListener('click', function () {
      sheet.classList.contains('open') ? close() : open();
    });
    sheet.querySelector('.msheet-bg').addEventListener('click', close);
    sheet.addEventListener('click', function (e) { if (e.target.tagName === 'A') close(); });
    document.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  }
  if (document.body) mount(); else document.addEventListener('DOMContentLoaded', mount);
})();
