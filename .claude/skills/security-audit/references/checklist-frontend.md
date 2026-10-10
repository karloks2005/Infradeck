# Kontrolna lista: frontend (`src/frontend/**`)

Čitaj `src/App.jsx` (velik), `StatisticsPage.jsx`, `FleetStatistics.jsx`,
`ContainerStatistics.jsx`, `ServerInsights.jsx`, `charts.jsx`, `distributions.jsx`,
`*Logic.js`, `chartMath.js`, `main.jsx`, `index.html`, `vite.config.js`, `package.json`.

## 1. XSS

Dashboard prikazuje podatke koje kontrolira napadač (imena kontejnera, putanje mountova,
poruke događaja, tekst asistenta, imena servera). **Stored XSS u admin kontekstu = preuzimanje
dashboarda**, pa je ovo glavna stavka.

- [ ] Pretraži: `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML`, `insertAdjacentHTML`,
      `document.write`, `eval`, `new Function`, `setTimeout("string")`, `srcdoc`, `postMessage`
      bez provjere origina. Svako pojavljivanje prati do izvora podatka.
- [ ] **Markdown/rich tekst** (izlaz asistenta, opisi): koristi li se biblioteka? Je li HTML
      isključen ili sanitiziran (DOMPurify s restriktivnom konfiguracijom)? Linkovi iz
      markdowna filtriraju `javascript:`/`data:`.
- [ ] **Atributi s URL-om iz podataka:** `href`, `src`, `action`, `formAction`: shema samo
      `http(s)` (i `mailto` ako treba). `target="_blank"` ima `rel="noopener noreferrer"`.
- [ ] SVG iz podataka (grafovi koji ugrađuju stringove u SVG, `<text>`, `<title>`, `<a>`):
      React escapea tekst, ali provjeri ručno sastavljene SVG stringove i `style` objekte
      s podacima.
- [ ] CSS injekcija: vrijednosti iz podataka u `style`/`className` konstruiranima iz stringova.
- [ ] Prototype pollution: spajanje objekata iz JSON-a s kolektora (`Object.assign`, spread
      u ključeve iz podataka, `obj[key] = ...` s ključem iz podataka, `__proto__`).

## 2. Autentikacija i tajne u pregledniku

- [ ] Tokeni/sesije se **ne** drže u `localStorage`/`sessionStorage`/URL-u (sesija je
      HttpOnly kolačić). Provjeri što se sprema u `localStorage` (osjetljivi podaci, tokeni,
      TOTP, recovery kodovi).
- [ ] Recovery kodovi i TOTP tajna: prikazuju se samo jednom, ne spremaju se u stanje koje
      preživljava navigaciju, ne završavaju u URL-u ni logu konzole (`console.log`).
- [ ] Nema API ključeva, `SECRETS_KEY`-a ni internih URL-ova ugrađenih u bundle
      (`import.meta.env.VITE_*` je **javno**). Pretraži `VITE_`.
- [ ] Frontend ne donosi sigurnosne odluke koje backend ne provjerava (skrivanje admin
      gumba nije kontrola pristupa; to pokriva backend lista, ovdje samo zabilježi).
- [ ] Odjava briše stanje u memoriji; nema keširanja osjetljivih odgovora (nginx/`Cache-Control`
      za API).

## 3. Generiranje naredbi za instalaciju ("Connect a server")

Panel generira naredbu koju korisnik kopira i izvršava na udaljenom stroju. Ona sadrži
join token i digest slike.

- [ ] Sve vrijednosti u naredbi (token, digest, URL, `COLLECTOR_BIND`, ime) su **validirane ili
      citirane za ljusku**. Vrijednost s `;`, `$(...)`, `` ` ``, `'`, novim redom ili razmakom
      ne smije izaći iz navodnika. Provjeri odakle vrijednosti dolaze (konfiguracija
      dashboarda, odgovor API-ja) i kako se spajaju u string.
- [ ] Naredba ne koristi `curl | sh` ni nepinirane slike; digest je obavezan, a prikaz
      jasno razlikuje što korisnik izvršava.
- [ ] Token se prikazuje jednom, ima vidljiv istek, a "Kopiraj" ne ostavlja ga u povijesti
      stanja.

## 4. Mrežne pozivne točke i CSRF

- [ ] `fetch` pozivi za mijenjanje stanja šalju kolačić (`credentials`) samo prema vlastitom
      originu; nema poziva prema proizvoljnim URL-ovima iz podataka.
- [ ] Nema otvorenog preusmjeravanja (`window.location = param`), nema `redirect`/`next`
      parametra bez provjere da je relativan put.
- [ ] Rukovanje greškama ne ispisuje stack/tajne korisniku; odgovori API-ja koji sadrže
      tuđe stringove prikazuju se kao tekst.

## 5. CSP i build

- [ ] Frontend radi pod strogim CSP-om bez `unsafe-inline` za skripte (nema inline `<script>`
      u `index.html`, nema `eval` u bundle-u). Novi izvori koji traže proširenje CSP-a
      moraju biti dokumentirani.
- [ ] `vite.config.js`: dev proxy/`server.host` ne izlaže dev server; `build.sourcemap` u
      produkciji isključen ili namjeran; nema `define` s tajnama.
- [ ] `index.html`: bez vanjskih skripti/fontova bez SRI-ja; bez inline handlera.
- [ ] Ovisnosti (`package.json`): neobične ili nepotrebne, `postinstall` skripte; vidi infra
      listu za audit alate.

## 6. Dostupnost kao sigurnost

- [ ] Renderiranje ne pada ni ne zamrzava preglednik na hostilnim podacima: vrlo dugi
      stringovi, tisuće kontejnera, `NaN`/`Infinity`/negativni brojevi u grafovima
      (`chartMath.js`, `fleetLogic.js`, `containerLogic.js`), prazni nizovi, nepoznata polja.
      Pad cijele aplikacije zbog jednog loše oblikovanog zapisa = napadač s kolektorom može
      onesposobiti dashboard.
