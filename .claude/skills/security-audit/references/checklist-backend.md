# Kontrolna lista: backend (`src/backend/**`)

Čitaj `main.py`, `auth.py`, `totp.py`, `secrets_box.py`, `rate_limit.py`, `rules.py`,
`models.py`, `db.py`, `config.py`, `history.py`, `rollups.py`, `stats.py`, `alerts.py`,
`seed_user.py`, `reset_2fa.py` u cijelosti. Za svaku stavku nađi kod koji je provodi.

## 1. Autentikacija i autorizacija (najveći prioritet)

- [ ] **Svaka ruta ima svjestan odgovor** na "tko smije?" (inventar ruta iz koraka 1). Ruta
      koja zaboravi `Depends(...)` za auth je zadano **otvorena**: traži takve.
- [ ] Razlika *anonimno / prijavljen / admin / kolektor* provodi se na serveru, nikad samo u
      frontendu. Admin-only rute stvarno provjeravaju ulogu (ne samo prijavu).
- [ ] **Ograničena sesija** (admin bez 2FA dok je politika uključena) stvarno može doći samo do
      profila, postavljanja 2FA i odjave. Provjeri *svaku* rutu, ne samo ono što test pokriva,
      uključujući nove rute dodane nakon te značajke.
- [ ] **IDOR / horizontalna autorizacija:** rute s ID-evima (server, kontejner, konverzacija,
      sesija, token, korisnik, pravilo, alarm) provjeravaju vlasništvo/ulogu. Posebno: popis
      sesija po javnim ID-evima, brisanje tuđe sesije, konverzacije drugog korisnika.
- [ ] **Mass assignment:** modeli zahtjeva ne dopuštaju polja kao `is_admin`, `role`,
      `require_2fa`, `server_id`, `owner_id`. Provjeri Pydantic modele i `**body`/`setattr`.
- [ ] Zadnji admin ne može obrisati/degradirati sam sebe do stanja bez admina (ili je to
      namjerno i dokumentirano). Admin ne može eskalirati drugog bez provjere.
- [ ] Prvi račun postaje admin: provjeri **sve** puteve (setup token, `ALLOW_REGISTRATION`,
      utrka dvaju istodobnih zahtjeva, `ADMIN_BOOTSTRAP_TOKEN` usporedba, brisanje tokena nakon
      prvog računa). Je li usporedba tokena konstantnog vremena (`hmac.compare_digest`)?

## 2. Sesije, kolačići, CSRF

- [ ] ID sesije: iz `secrets` (ne `random`/`uuid1`), dovoljna entropija, pohranjen hashiran ili
      barem nikad vraćen pregledniku u tijelu odgovora. Rotacija pri promjeni privilegija
      (prijava, 2FA, promjena lozinke).
- [ ] Zastavice kolačića: `HttpOnly`, `Secure` (i u kojim uvjetima se gasi: provjeri da
      zadana vrijednost nije nesigurna), `SameSite`, `Path`, istek.
- [ ] Istek i odjava stvarno brišu sesiju na serveru; logout-everywhere radi.
- [ ] **Provjera `Origin`/`Sec-Fetch-Site`** na POST/PUT/PATCH/DELETE: pokušaj zaobilaženja:
      `Origin` s istim prefiksom (`https://dash.example.com.evil.tld`), različit port/shema,
      `Origin: null`, nedostajući `Origin` + `Sec-Fetch-Site: same-site`, zaglavlje `Host`
      pod kontrolom napadača, `CORS_ORIGINS` s džokerima. Radi li provjera za **sve** metode
      i sve rute (middleware ili po ruti)? Postoji li izuzeće za puteve kolektora i može li ga
      se zloupotrijebiti da bi se zaobišla provjera na ruti korisnika?
- [ ] CORS: `allow_origins` nije `*` uz `allow_credentials=True`; nema refleksije proizvoljnog
      `Origin`.

## 3. Lozinke, 2FA, tokeni

- [ ] Hashiranje lozinke: bcrypt/argon2 s razumnim troškom; granica 72 B provodi se (odbija,
      ne skraćuje); nema lozinki u logovima; promjena lozinke traži staru lozinku (ili je
      sesija svježa) i poništava ostale sesije.
- [ ] **Timing/enumeracija računa:** prijava, reset, registracija vraćaju iste odgovore i
      slično vrijeme za nepostojeći email i pogrešnu lozinku (fiktivni hash za nepostojeće
      račune?).
- [ ] **Rate limit** (`rate_limit.py`): ključ (IP, email) ne može se zaobići promjenom
      zaglavlja (`X-Forwarded-For` iz nepouzdanog izvora), velikim/malim slovima ili
      razmacima u emailu, unicode normalizacijom; memorija ograničena (napadač ne može
      napuhati strukturu milijunima ključeva); rade li limiti pod više radnih procesa
      (stanje u memoriji po procesu = efektivni limit × broj procesa)?
- [ ] **TOTP** (`totp.py`): usporedba konstantnog vremena; prozor ±1; **zabrana ponovne
      uporabe istog koda je atomarna** (jedinstveni indeks/UPDATE s uvjetom, ne
      čitaj-pa-piši); brojač pokušaja po računu i po adresi ne resetira se ponovnim
      početkom lozinke; međutoken (5 min) je jednokratan, vezan uz korisnika i ne vrijedi
      kao sesija nigdje.
- [ ] **Recovery kodovi:** entropija, hashirani, jednokratni *atomarno*, brišu se/ponovno
      generiraju pri isključivanju 2FA.
- [ ] `secrets_box.py`: Fernet ključ iz okoline, ponašanje bez ključa ne smije ostaviti
      tajne u čistom tekstu; greška dekripcije ne curi detalje; ključ se ne logira.
- [ ] **Tokeni servera i join tokeni:** generirani iz `secrets`, hashirani (SHA-256 je u redu
      za tokene visoke entropije), usporedba konstantnog vremena ili pretraga po hashu,
      jednokratnost i istek **atomarni**, ograničenje otvorenih tokena (20) provodi se i pod
      paralelnim zahtjevima, brisanje servera poništava token.
- [ ] Ponovno uparivanje postojećeg stroja (isti machine ID) traži `replace: true` na
      serveru, ne samo u UI-ju; mogu li se vjerodajnice tuđeg servera prepisati bez toga?

## 4. Ulazi koje šalje kolektor (nepouzdani)

- [ ] Svaka ruta kolektora (register, poll, enroll, ingest, helper događaji) odbija se kad
      zahtjev nije došao kroz gate (`X-Collector-Via` / `COLLECTOR_ACCESS=strict`); provjeri
      da klijent ne može sam poslati to zaglavlje preko javne strane (nginx ga mora
      prebrisati/ukloniti, backend ga ne smije slijepo vjerovati ako je backend dostupan
      drugim kontejnerima).
- [ ] Autentikacija kolektora: token po serveru; kolektor jednog servera ne može pisati
      podatke drugog (`server_id` iz tokena, ne iz tijela zahtjeva!).
- [ ] **Validacija:** tipovi, duljine, rasponi, strogi parsing vremena, `sha256:`+64 hex,
      maks. 500 kontejnera, imena ≤128, ograničenja događaja po check-inu i po satu. Traži
      polja koja se spremaju bez čišćenja (nova polja dodana kasnije često zaobiđu).
- [ ] Veličina tijela zahtjeva ograničena (nginx `client_max_body_size` **i** Pydantic
      `max_length`), dubina/veličina JSON-a, broj elemenata lista.
- [ ] Idempotentnost/replay: ponovljeni check-in ne stvara duplicirane incidente ni neograničen
      rast tablica. Hostilni podaci ne mogu "utišati" alarme drugih servera.

## 5. Baza i injekcije

- [ ] Svaki `text(...)`, `execute(f"...")`, `.format`, `%` ili spajanje stringova u SQL-u:
      jesu li vrijednosti vezane parametrima? Dinamički `ORDER BY`, imena stupaca/metrika i
      `LIMIT` iz zahtjeva idu samo kroz **bijelu listu**.
- [ ] Imena kontejnera i putanje mountova ulaze u ključeve incidenata/cache ključeve: mogu
      li se sudarati ili ubrizgati separator (`:`, `|`, newline) i spojiti dva incidenta?
- [ ] LIKE upiti s korisničkim unosom (escape `%`/`_`), regex iz korisničkog unosa (ReDoS;
      `rules.py` ima 593 retka pravila, provjeri koje izraze evaluira i je li to sigurno).
- [ ] **`rules.py`: izvršava li se išta dinamički** (`eval`, `exec`, `compile`, `getattr` s
      korisničkim imenom, `importlib`, šabloni stringova s korisničkim formatom)? Ako se
      pravila/alarmi konfiguriraju korisnički, što sve konfiguracija smije izraziti?
- [ ] Ograničenja stranica/`limit`, broj redaka, vremenski rasponi na `history`/`stats`
      (fiksna lista raspona i metrika, ograničen cache, ključevi iz validiranih vrijednosti).
- [ ] Transakcije: nema stanja u kojem pad usred operacije ostavlja račun bez 2FA ili sesiju
      koja preživljava promjenu lozinke.

## 6. Izlazne veze (SSRF) i integracije

- [ ] Kanali obavijesti (`notify.py`: ntfy, Discord, webhook, email) postavljaju se preko API-ja
      po korisniku: shema samo http(s), loopback/link-local/metadata uvijek odbijeni, privatne
      LAN adrese samo za admine, provjera nakon DNS razrješenja i spajanje na provjerenu adresu
      (DNS rebinding), bez praćenja preusmjeravanja, timeout. Provjeri i SMTP put.
- [ ] Sadržaj odlazne poruke ne sadrži tajne (tokene, hashove) ni neocišćene tuđe stringove u
      kontekstu gdje se interpretiraju (Slack/markdown/HTML/email zaglavlja → injekcija).
- [ ] Nema `requests`/`httpx` poziva prema URL-u iz zahtjeva bez gornjih zaštita.

## 7. Konfiguracija, tajne, logiranje, greške

- [ ] `config.py`/okolina: nesigurne zadane vrijednosti (prazan `SECRETS_KEY`,
      `ALLOW_REGISTRATION=true`, `COLLECTOR_ACCESS` ≠ strict, `DEBUG`, `CORS_ORIGINS=*`).
      Zadano mora biti sigurno, a nesigurno mora zahtijevati namjernu promjenu.
- [ ] Tajne se ne ispisuju u logove, iznimke ni odgovore (setup token se ispisuje **namjerno**
      pri startu dok nema računa; provjeri da se ne ispisuje poslije). Nema stack traceova ni
      SQL-a u HTTP odgovorima; nema `/docs`/`/openapi.json` izloženih na javnoj strani ako to
      nije namjera (provjeri `FastAPI(docs_url=...)` i nginx).
- [ ] **Log injection:** korisnički i kolektorski stringovi u logovima s novim redom/ANSI
      sekvencama.
- [ ] `seed_user.py` i `reset_2fa.py`: bez ugrađenih vjerodajnica, bez ispisa lozinke, ne
      mogu se pokrenuti preko API-ja, ne ostavljaju tajne u povijesti ljuske (argumenti).
- [ ] Pokretanje: backend se ne pokreće s `--reload`/debug u produkcijskoj slici; radi kao
      neprivilegirani korisnik (vidi infra listu).

## 8. Dostupnost (DoS jednim korisnikom)

- [ ] Skupi endpointi (statistika, flota, povijest, eksport) imaju ograničenje rada i cache;
      nema neograničenih petlji nad podacima koje kontrolira kolektor.
- [ ] Dugotrajne operacije ne blokiraju event loop (sinkroni DB/CPU rad u `async def`
      ruti blokira sve zahtjeve: jedan spor zahtjev = zastoj).
- [ ] Rast podataka: tablice koje se pune iz check-inova imaju gornju granicu ili retenciju;
      zabilježi ako je rast neograničen (poznato za sažetke povijesti).
