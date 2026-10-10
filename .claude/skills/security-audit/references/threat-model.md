# Model prijetnji (Homelab Agent)

Sažetak arhitekture i povjerenja, izveden iz `SECURITY.md` i koda. Ako se kod i ovaj dokument
razilaze, **vjeruj kodu** i to prijavi kao nalaz o dokumentaciji.

## Komponente i što dodiruju

| Komponenta | Gdje | Doseže | Povjerenje |
|---|---|---|---|
| Frontend (nginx + React SPA) | `docker/nginx*.conf`, `src/frontend` | javni listener; proxy prema backendu | javno dostupan |
| Backend (FastAPI) | `src/backend` | Postgres, interni mrežni segment | obrađuje sve ulaze |
| Postgres | `docker/postgres/init` | samo backend | sadrži hashove, šifrirane TOTP tajne, podatke o hostovima |
| Collector gate | `docker/nginx.collector.conf` | backend; jedan port na VPN adresi | jedini put do collector endpointa |
| Collector | `src/agent/collector.py` | `/proc` hosta, host FS read-only (`/hostfs`), Docker proxy, backend preko HTTP-a | pokreće se na udaljenim hostovima, može biti kompromitiran |
| Docker proxy | `src/agent/docker_proxy.py` | Docker socket (uid 0), Unix socket prema collectoru | drži root-ekvivalentan pristup; mora ostati minimalan |
| System helper (opcionalno) | `src/agent/system_helper.py` | kernel log, wtmp, `CAP_SYSLOG` | privilegiran; njegov izlaz je nepouzdan |
| LLM asistent | `src/agent/service.py`, `prompts.py`, `SYSTEM_PROMPT.md`, `health_checks.py` | fiksni read-only provjere, opcionalni webhook | ulaz uključuje podatke s hosta |
| CI | `.github/workflows/images.yml` | GHCR, cosign | opskrbni lanac za slike koje hostovi izvršavaju |

Izvan repozitorija (nisu u opsegu, ali ih treba imati na umu kao pretpostavke): Traefik pred
frontendom, OpenVPN koji nosi promet kolektora, GHCR paketi.

## Profili napadača

Za svaki nalaz odredi koji ga profil može iskoristiti.

1. **Anonimni korisnik interneta** na javnom listeneru. Doseže samo dashboard i njegov API.
   Collector endpointi (register, poll, enroll, ingest) na toj strani **ne smiju postojati**.
2. **Prijavljeni korisnik bez admin prava.** Po dizajnu vidi sve monitoring podatke svih
   servera. Pitanje je može li *mijenjati* išta, vidjeti tuđe račune/sesije/konverzacije,
   ili postati admin.
3. **Susjedna web aplikacija na istoj domeni/podlozi** (sibling subdomain iza istog reverse
   proxyja). Ista "site", pa `SameSite=Lax` ne štiti od zahtjeva koji mijenjaju stanje.
   Zaštita je provjera `Origin` / `Sec-Fetch-Site`.
4. **Peer na VPN-u.** Doseže collector gate. Bez valjanog join tokena ne smije ni zatražiti
   pridruživanje; s njim čeka admin odobrenje; ingest traži token servera.
5. **Kompromitirani kolektor.** Kontrolira sve što prijavljuje (imena kontejnera, mountovi,
   portovi, događaji). Backend mu ne smije vjerovati: tipovi, duljine, rasponi, brojevi,
   limiti po check-inu i po satu. Ne smije moći utjecati na druge servere ni na dashboard.
6. **Kompromitirani ili zloćudni kontejner na hostu / susjed na Docker mreži.** Može
   pokušati doći do backenda mimo nginxa (lažiranje `X-Collector-Via`, `X-Forwarded-For`),
   do Unix socketa docker proxyja ili do volumena koji ne smije dijeliti.
7. **Kompromitirani dashboard.** Ne smije moći natjerati hostove da izvrše kod (dashboard
   ne poslužuje kod), ni usmjeriti sondiranja na proizvoljne mete (mete dolaze iz okoline
   kolektora).
8. **Opskrbni lanac.** Ovisnosti (pip, npm), bazne slike, GitHub Actions, workflow
   dozvole, nepodpisane ili nepinirane slike.
9. **Hostilni podaci s nadziranog hosta.** Imena kontejnera, putanje mountova, linije
   kernel loga, wtmp, `/etc/os-release`. Napadač s ograničenim pristupom hostu može
   utjecati na te stringove. Putovi: stored XSS, injekcija u ključeve/upite, prekoračenje
   limita, razbijanje parsera, **prompt injection** prema asistentu.
10. **Fizički/lokalni pristup hostu** (npr. `seed_user.py`, `reset_2fa.py`) je izvan modela
    prijetnji po dizajnu: ta ovlast već daje pristup bazi. Ali skripte ne smiju imati
    ugrađene vjerodajnice ni curiti tajne.

## Najvrjednija imovina (redom)

1. Pristup Docker API-ju / rootu na hostovima (preko proxyja, helpera, mountova).
2. Admin sesija dashboarda i mogućnost odobravanja novih servera.
3. Tokeni servera i join tokeni (hashirani u bazi).
4. `SECRETS_KEY` (Fernet) i TOTP tajne, recovery kodovi.
5. Monitoring podaci (otvoreni portovi, firewall hash, popis kontejnera) jer mapiraju mrežu.
6. Integritet alarma (napadač koji može utišati ili lažirati incidente skriva napad).

## Pretpostavke dizajna koje treba provjeriti, ne uzeti zdravo za gotovo

- Backend vjeruje `X-Collector-Via` samo ako ništa nepouzdano ne doseže backend izravno.
- nginx prihvaća `X-Forwarded-For` samo od privatnih/Docker adresa; rate limiti ovise o tome.
- Collector endpointi odbijaju se i na javnoj strani (nginx 404) i u backendu
  (`COLLECTOR_ACCESS=strict`). Provjeri **oba** sloja i da ni jedan ne ovisi o drugom.
- "Jednokratno" (join tokeni, TOTP kodovi, recovery kodovi, setup token) mora biti atomarno
  u bazi, ne "provjeri pa označi" (utrka).
- "Ograničeno" (500 kontejnera, 20 otvorenih tokena, 5 pokušaja/5 min) mora vrijediti i pod
  paralelnim zahtjevima.
- Prvi račun postaje admin: provjeri sve puteve do tog stanja (registracija, setup token,
  `seed_user.py`, utrka dvaju istodobnih prvih zahtjeva).

## Poznata prihvaćena ograničenja (ne prijavljuj kao nova, ali provjeri da još vrijede)

Iz `SECURITY.md`: svi prijavljeni računi vide sve monitoring podatke; zaključavanje 2FA koraka
pri pogađanju kodova; sesije prije uvođenja 2FA nisu u popisu; samo TOTP; promet VPN-om je
običan HTTP unutar tunela; sažeci povijesti se još ne brišu. Ako kod odstupa od onoga što je
tamo opisano, to je nalaz.

## Otvorene stavke iz projektne bilješke (provjeri jesu li još otvorene)

Ograničenje broja čekajućih servera po IP-u; rate limit na `enroll`; `src/backend/seed_user.py`
(ugrađena admin lozinka); `dev.db` pod gitom; zastarjeli README. Ako su još stvarne, prijavi ih
s trenutnim stanjem u kodu.
