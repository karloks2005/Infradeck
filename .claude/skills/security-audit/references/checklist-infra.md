# Kontrolna lista: infrastruktura, repozitorij, ovisnosti

Čitaj `docker/*` (Dockerfile-ove, `nginx*.conf`, `Caddyfile`, `postgres/init/*.sql`),
`compose.yml`, `docker-compose.server.yml`, `collector-compose.yml`, `example.env`,
`.dockerignore`, `.gitignore`, `.github/workflows/*.yml`, `requirements*.txt`,
`src/frontend/package.json` + lockfile.

## 1. Dockerfile-ovi

- [ ] `USER` je neprivilegiran u svakoj slici koja to može (docker-proxy i system-helper su
      iznimke zbog socketa/capabilityja: provjeri da je to jedino mjesto i da je obrazloženo).
- [ ] Bazne slike: pinirane (digest ili barem točna verzija), ne `latest`; minimalne.
- [ ] Nema tajni u slojevima (`ARG`/`ENV` s tokenima, `COPY .env`, `.git`, `dev.db`, ključevi);
      `.dockerignore` isključuje `.env*`, `.git`, `venv`, `*.db`, testove, `node_modules`.
- [ ] Nema `curl | sh`, `pip install` bez pinova/hashova za produkcijske ovisnosti, `apt` bez
      `--no-install-recommends` i čišćenja; build ne izvršava nepouzdani kod s mreže.
- [ ] `HEALTHCHECK`/`CMD` ne pokreće debug način (`--reload`, `--debug`).
- [ ] Frontend slika: izgrađena u multi-stage, u produkciji nema `node_modules`, dev servera
      ni source mapa koje otkrivaju izvorni kod ako to nije namjera.

## 2. Compose datoteke

Za **svaki servis** zapiši: korisnik, `cap_drop`/`cap_add`, `read_only`,
`security_opt: no-new-privileges`, `privileged`, `network_mode`, `pid`, `ports`, `volumes`,
`environment`/`secrets`.

- [ ] **Objavljeni portovi** vezani su na konkretnu adresu (`127.0.0.1:` ili VPN adresa), ne na
      `0.0.0.0`, osim namjerno javnog frontenda. Docker zaobilazi `ufw`: firewall pravilo nije
      zaštita. Backend, Postgres i docker-proxy nemaju objavljenih portova u server stogu.
- [ ] **`/var/run/docker.sock` montiran samo u docker-proxy**, nigdje drugdje (backend,
      collector, frontend, helper). Isto za `/`, `/etc`, `/root`, `/home`, `/var/lib/docker`.
- [ ] Mountovi hosta su `:ro` gdje je moguće; `/hostfs` je read-only; nema `rw` mounta
      povjerljivih direktorija.
- [ ] Volumen s Unix socketom docker-proxyja dijele **samo** proxy i collector.
- [ ] `cap_drop: [ALL]` kao zadano; svaki `cap_add` obrazložen (samo `CAP_SYSLOG` za helper).
      Nema `privileged`, `pid: host`, `network_mode: host`, `ipc: host`, `devices` bez razloga.
- [ ] `read_only: true` + `tmpfs` za potrebno; `no-new-privileges` na svemu.
- [ ] Mrežna segmentacija: collector-gate nije na mreži reverse proxyja; backend nije na
      mreži kojoj imaju pristup tuđe aplikacije; Postgres samo na internoj mreži;
      `internal: true` gdje izlaz nije potreban.
- [ ] **Zadane/placeholder tajne:** u compose/`example.env` nema stvarnih ili zadano slabih
      lozinki (`changeme`, `postgres`, `admin`) koje bi ostale u produkciji; obavezne
      vrijednosti koriste `${VAR:?poruka}` da stog ne krene bez njih.
- [ ] Slike u compose-u za udaljene hostove pinirane po digestu (`COLLECTOR_IMAGE`,
      `DOCKER_PROXY_IMAGE`), a panel ne generira naredbu bez njih.
- [ ] Postgres: lozinke ne u compose-u u čistom tekstu ako se može `secrets:`/env iz `.env`;
      nema objavljenog 5432.
- [ ] Healthchecki ne otkrivaju tajne; restart politike ne skrivaju petlju rušenja.

## 3. nginx (`nginx.conf`, `nginx.strict.conf`, `nginx.collector.conf`)

- [ ] **Javna strana vraća 404 za sve collector endpointe** (register, poll, enroll, ingest,
      helper). Provjeri točne `location` blokove: prefiks vs. točno, regex, razlike u
      kosoj crti, velika/mala slova, `//`, `/./`, `%2f`, `..;/`. Najgori slučaj: put koji
      nginx ne prepoznaje kao zabranjen, a backend ga normalizira u collector endpoint.
- [ ] `X-Collector-Via` (i slična interna zaglavlja) **postavlja** samo gate, a javna strana
      ga **briše/prepisuje** (`proxy_set_header X-Collector-Via "";`).
- [ ] `real_ip` / `X-Forwarded-For`: `set_real_ip_from` samo privatni/Docker rasponi,
      `real_ip_recursive` ispravno; nema povjerenja u XFF s javne strane; backend dobiva
      stvarni klijentski IP za rate limit.
- [ ] **`add_header` nasljeđivanje:** `add_header` u `location` bloku *poništava* sve
      `add_header` iz `server`/`http` razine. Provjeri da svaki `location` koji ima vlastiti
      `add_header` (cache, CORS) ponavlja sigurnosna zaglavlja (CSP, nosniff, frame, HSTS),
      i da su dodana s `always` kako bi vrijedila i za greške.
- [ ] CSP: bez `unsafe-inline`/`unsafe-eval` za `script-src`, `default-src 'self'`,
      `frame-ancestors 'none'`, `object-src 'none'`, `base-uri 'self'`, `form-action 'self'`;
      dopušteni `style` inline je poznata cijena (React style atributi).
- [ ] HSTS samo na strict/javnoj konfiguraciji; `server_tokens off`.
- [ ] `client_max_body_size` ograničen po lokaciji; `client_body_timeout`, `proxy_*_timeout`,
      `limit_req`/`limit_conn` na osjetljivim rutama (login, enroll) kao drugi sloj.
- [ ] `proxy_pass`: pazi na kosu crtu na kraju (promjena normalizacije puta), prosljeđuju se
      samo potrebna zaglavlja, `proxy_set_header Host` ispravan (backend koristi `Host` za
      provjeru Origina), WebSocket upgrade samo gdje treba.
- [ ] Nema `autoindex on`, `alias` bez kose crte (traversal), poslužuje li se išta osim
      statičkih datoteka SPA-a (`.map`, `.env`, `.git`)? `try_files` ne otkriva skrivene datoteke.
- [ ] TLS konfiguracija (ako je nginx terminira): protokoli ≥ TLS1.2, bez slabih šifri.
      Ako TLS terminira Traefik, navedi to kao pretpostavku, ne nalaz.
- [ ] `Caddyfile`: je li to ostatak/dev konfiguracija? Ako je u produkcijskom putu, pregledaj
      jednako strogo kao nginx.

## 4. Postgres (`docker/postgres/init/*.sql`)

- [ ] Aplikacija se spaja kao korisnik s **minimalnim pravima** (nije superuser, nije vlasnik
      sheme ako migracije nisu potrebne pri radu), odvojen od administrativnog.
- [ ] Lozinke u init skriptama nisu ugrađene u repozitorij; nema `trust` autentikacije iz
      svih adresa; `pg_hba` ne dopušta vanjske veze.
- [ ] Nema `CREATE EXTENSION` ni `GRANT ALL` bez razloga.

## 5. GitHub Actions (`.github/workflows/images.yml`)

Ovo je opskrbni lanac slika koje hostovi pokreću s root-ekvivalentnim pristupom.

- [ ] `permissions:` najmanje potrebno (`contents: read`, `packages: write`, `id-token: write`
      samo za job koji potpisuje); nema zadanih širokih dozvola.
- [ ] **Akcije pinirane na puni commit SHA**, ne na tag (`@v4`) za akcije koje diraju tajne ili
      potpisivanje; provjeri tko ih održava.
- [ ] Nema `pull_request_target` s checkoutom koda iz PR-a; okidač je samo tag `v*` i/ili
      ručni, ne svaki push/PR iz forka.
- [ ] **Script injection:** nema `${{ github.event.* }}` / `${{ github.head_ref }}` /
      imena grane ili poruke commita umetnutih izravno u `run:` (prenesi preko `env:`).
- [ ] Potpisivanje cosignom: *keyless* identitet vezan uz workflow i ref; ispis digesta
      točan; slike se grade iz tagiranog commita; build ne povlači nepinirane ovisnosti bez
      provjere. Opiši kako domaćin **provjerava** potpis (dokumentirano ako `cosign verify`
      nije dio generirane naredbe, to je slabost: potpis koji nitko ne provjerava ne štiti).
- [ ] Tajne se ne ispisuju (`set -x`, `echo`); nema `secrets: inherit` bez razloga.

## 6. Repozitorij i povijest

- [ ] `.gitignore` pokriva `.env`, `*.db`, `*.sqlite`, ključeve, `venv`, `node_modules`.
      `git ls-files | grep -Ei '\.env$|\.db$|\.sqlite|\.pem$|\.key$|id_rsa|secret|credential'`:
      što je **pod gitom**? (`dev.db` je ranije bio.) `.env` i `example.env` obje postoje:
      provjeri da `example.env` nema stvarne vrijednosti.
- [ ] **Povijest:** `git log --all --diff-filter=A --name-only | grep -Ei '\.env|\.db|key|secret'`
      i pretraga uzoraka tajni (`git log -p --all -S'SECRET' -S'PASSWORD' -S'TOKEN'` na
      razumnom opsegu). Tajna koja je ikad bila u povijesti smatra se kompromitiranom čak i
      ako je kasnije obrisana; to treba prijaviti uz preporuku rotacije. Ne ispisuj vrijednosti.
- [ ] Pretraga uzoraka u radnom stablu: ključevi (`AKIA`, `ghp_`, `sk-`, `-----BEGIN`),
      lozinke u kodu, hardkodirani tokeni, `localhost` zaobilaženja autentikacije,
      `TODO`/`FIXME`/`HACK` uz riječi `auth`, `security`, `bypass`, `skip`.
- [ ] `TESTS.md`, `docs/`, `README.md` ne sadrže stvarne tajne/domene/IP adrese vlasnika.
- [ ] `SECURITY.md` ima put za prijavu ranjivosti (ima) i točne tvrdnje.

## 7. Ovisnosti

- [ ] `requirements*.txt` i `package-lock.json`: pinirano? Lockfile usklađen s `package.json`?
- [ ] Ako su dostupni alati (`pip-audit`, `npm audit --omit=dev`), pokreni ih **samo uz
      korisnikovu potvrdu** (šalju popis paketa prema registryju). Ako nisu pokrenuti,
      napiši to u pokrivenosti: nemoj tvrditi da su ovisnosti "bez poznatih ranjivosti".
- [ ] Nepotrebne/napuštene ovisnosti, ovisnosti iz URL-a/gita, `postinstall` skripte,
      typosquat imena. Dev ovisnosti ne ulaze u produkcijsku sliku.
