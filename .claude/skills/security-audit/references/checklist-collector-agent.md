# Kontrolna lista: collector, docker proxy, system helper (`src/collector/**`), incident agent (`src/agent/**`)

Čitaj `collector.py`, `docker_proxy.py`, `system_helper.py` te za agenta `src/agent/` (`tools/`,
`investigate.py`, `diagnosis.py`, `chat.py`, `masking.py`, `safety.py`, `providers/`, `worker.py`,
`usage.py`, oba prompta) u cijelosti. Docker proxy i system
helper drže privilegije: najveća pažnja ide njima.

## 1. Docker proxy (`docker_proxy.py`): drži root-ekvivalent

- [ ] **Bijela lista je doslovna usporedba cijelog request targeta.** Nema dekodiranja,
      normalizacije, `startswith`, regexa bez sidra (`re.match` bez `$`/`fullmatch`), ni
      `in`. Pokušaji zaobilaženja: `?all=1&x=y`, `//containers/json`, `/containers/json/`,
      `%2e`, `;`, `#`, `\`, razmak, tab, `\r\n`, velika/mala slova metode, `HEAD`/`OPTIONS`,
      `GET` s tijelom, `Transfer-Encoding`/`Content-Length` proturječja, HTTP/1.0,
      više zahtjeva na jednoj vezi (pipelining/smuggling), ID kontejnera koji nije hex
      (`../`, `x/exec`).
- [ ] ID u `/containers/<id>/json` i `/stats` strogo validiran (hex, duljina) *prije*
      prosljeđivanja u Docker. Proxy ne gradi zahtjev prema Dockeru spajanjem neprovjerenog
      stringa.
- [ ] Proxy **ne prosljeđuje** zaglavlja ni metodu klijenta; zahtjev prema Dockeru gradi sam.
- [ ] **Filtriranje odgovora:** vraća samo fiksnu listu polja; `Env`, `Labels`, `Mounts`,
      `Config`, `HostConfig`, `Error` ne izlaze. Provjeri i za `/containers/json` i za
      stats (stats ne sadrži tajne, ali provjeri). Nova polja = namjerna promjena + test.
- [ ] Veličina odgovora/zahtjeva ograničena; timeouti; jedna sporo-čitajuća veza ne blokira
      sve (DoS prema kolektoru); greške Dockera ne vraćaju sirovi tekst.
- [ ] Unix socket: mode 0660, direktorij/volumen dijeljen **samo** s kolektorom; socket se
      ne stvara u svijetu-zapisivom direktoriju; nema utrke pri `bind`/`chmod` (kratak
      prozor s širim pravima; zadani `umask`).
- [ ] Nema TCP listenera, nema mreže (`network_mode: none`, vidi infra listu).
- [ ] Log 403 ne dopušta log injection (sanitiziran request target).

## 2. System helper (`system_helper.py`): privilegiran, izlaz nepouzdan

- [ ] Samo čita (dmesg *read-all*, wtmp), nikad ne piše/briše kernel log.
- [ ] Parsiranje wtmp-a je binarno: provjeri veličinu zapisa, granice, nepotpun zapis,
      ogroman broj zapisa; čita samo boot/shutdown zapise, ne korisnička imena ni adrese.
- [ ] Linije kernel loga: fiksni obrasci, reducirano na printable, skraćeno, ograničen broj
      događaja; nema `eval`/`shell`/format stringa s tim sadržajem; regexi bez katastrofalnog
      backtrackinga na 100 kB retku.
- [ ] Izlazna datoteka: atomarno pisanje (temp + rename), prava, samo u svom volumenu.
- [ ] Ništa se ne izvršava iz sadržaja; nema `subprocess` s ulazom pod kontrolom napadača
      (i ako postoji poziv, argumenti su lista, bez `shell=True`).

## 3. Collector (`collector.py`, 974 retka): radi na hostu, čita host FS

- [ ] **Čitanje datoteka s hosta:** fiksni popis putanja; `O_NOFOLLOW`; ograničena veličina
      čitanja (1 MB firewall datoteke, helperov izlaz); provjera da je helper izlaz svjež i
      iz tekućeg boota; nema putanja sastavljenih od podataka s hosta ili dashboarda
      (`os.path.join` s apsolutnom putanjom tiho zamjenjuje bazu!). TOCTOU između `stat` i
      `open`.
- [ ] `/proc/1/net` i ostali parseri: nepotpuno čitanje → "unknown", nikad djelomičan
      rezultat koji bi ports "zatvorio"; ogromne datoteke ne troše memoriju.
- [ ] **Ono što stiže s dashboarda** (odgovor na register/poll) tretira se nepouzdano: nema
      izvršavanja, nema putanja, nema ciljeva za sondiranje iz odgovora. Mete provjere
      interneta samo iz okoline kolektora (`INFRADECK_CONNECTIVITY_TARGETS`, ≤5, hostname ili
      IPv4), s tvrdim rokom.
- [ ] Sondiranje: ne šalje podatke, samo TCP connect + DNS; ne može se usmjeriti na
      proizvoljnu metu; ograničenje trajanja kako check-in ne bi visio.
- [ ] **Pohrana vjerodajnica kolektora** (token servera): datoteka s modom 0600, ne u
      slici, ne u logu, ne u argumentima procesa; kolektor ne ispisuje token pri pogrešci.
- [ ] Transport: provjera TLS-a ako je HTTPS (ne `verify=False`); kad je običan HTTP unutar
      VPN-a, to je dokumentirano. Nema preusmjeravanja koja bi odnijela `Authorization`
      zaglavlje na drugi host. Timeouti na svim mrežnim pozivima.
- [ ] Greška jednog uzorka ne ruši cijeli kolektor na način koji ga ušutkava bez traga
      (napadač koji izaziva iznimku "gasi" nadzor?).
- [ ] `subprocess`, `os.system`, `eval`, `exec`, `pickle`, `yaml.load`: svako pojavljivanje
      pogledaj; argumenti kao lista; nema podataka s hosta u naredbi.
- [ ] Ograničenja količine prijavljenih podataka (kontejneri, portovi, događaji) na strani
      kolektora, ne samo backenda.

## 4. Incident agent (`src/agent/`)

Dokumentirano (`docs/assistant.md`, `docs/agent-investigation.md`, SECURITY.md): agent nema
shell ni pristup hostu. Ima zatvoren popis read-only alata (`tools/__init__.py`: `server_info`,
`host_metrics`, `list_containers`, `container_history`, `disk_usage`, `events`,
`compare_to_baseline`, `find_incidents`, `fleet_snapshot`, `check_results`, `network_state`)
koji čitaju samo bazu Infradecka. Petlja (`investigate.py`) ima budžet poziva, rok i mjesečni
limit troška. Preporučene naredbe su samo tekst koji `safety.py` provjerava; ništa se ne izvršava.

- [ ] **Alati su doista fiksni:** `run` odbija nepoznato ime, argumenti se provjeravaju prema
      shemi prije čitanja; nema argumenta koji završava u naredbi, putanji, URL-u ili SQL-u
      izvan ORM-a. Rezultat je ograničen (`bounds.py`, `MAX_RESULT_CHARS`) i ne sadrži ništa
      što vide samo admini (IP adrese, korisnici, audit log), jer isti odgovor dobiva svatko.
- [ ] **Maskiranje:** sve što ide pružatelju (rezultati alata, kontekst incidenta, poruke
      korisnika u chatu, greške u `usage`/`worker`) prolazi `masking.py`. Traži put koji ga
      zaobilazi (novi alat, novo polje, poruka greške pružatelja koja se sprema ili prikazuje).
- [ ] **Prompt injection:** podaci koji ulaze u kontekst modela (imena kontejnera, događaji,
      greške provjera, tekst korisnika) mogu sadržavati upute. Što najgore model može ako ih
      posluša? (Potrošiti budžet, citirati krive činjenice, preporučiti štetnu naredbu.)
      Provjeri da `check_answer` prihvaća samo ID-eve činjenica iz stvarnih rezultata, da
      `safety.py` hvata destruktivne i skrivene znakove (bidi, zero-width) i da se naredba
      koja briše podatke nikad ne preporučuje.
- [ ] **Pružatelj i ključ:** ključ šifriran (`secrets_box`), nikad u odgovoru API-ja, logu ni
      audit zapisu; adrese pružatelja fiksne u kodu (korisnik ne može postaviti `base_url`);
      promjena pružatelja, ključa i uključivanje chata traže reauth (lozinka + 2FA).
- [ ] **Posao u pozadini:** worker (`jobs.py`, `worker.py`) ima ukupni rok i ne može ostaviti
      posao zauvijek u stanju "running"; red u Redisu ne prima nepouzdane podatke kao kod.
- [ ] **Izlaz modela se ne tretira kao pouzdan:** prikazuje se kao tekst (frontend ne radi
      `dangerouslySetInnerHTML`/neprovjeren markdown s HTML-om), ne ulazi u SQL/naredbe/URL-ove.
- [ ] **Konverzacije su vezane uz korisnika** (IDOR: čitanje/brisanje tuđih konverzacija
      po ID-u); limit duljine poruke i broja poruka; limit koraka petlje alata i vremena
      (DoS i trošak API-ja); rate limit po korisniku.
- [ ] API ključ prema LLM pružatelju: iz okoline, ne u logovima, ne u odgovorima, ne u
      konverzacijama koje se pohranjuju ili šalju natrag.
- [ ] Sistemski prompt ne sadrži tajne (pretpostavi da će ga model prije ili poslije
      ispisati).
- [ ] Pozivi prema Docker API-ju idu samo kroz proxy (`DOCKER_SOCKET_PATH`); backend ne montira
      `docker.sock`. Provjeri compose i kod.
- [ ] Agent nema izlaznih veza osim prema pružatelju; obavijesti šalje backend
      (`notify.py`, vidi `checklist-backend.md` §6), a model ne bira odredište ni sadržaj.
