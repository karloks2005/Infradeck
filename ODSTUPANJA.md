# Odstupanja od dizajna

Popis svega što se u implementaciji redizajna razlikuje od PDF-ova u `design/web/` i `design/mobile/`,
s razlogom i, gdje postoji, GitHub issueom. Stanje: 14. 10. 2026.

Mobilni prikazi (`design/mobile/`) napravljeni su 14. 10. 2026; njihova odstupanja su u odjeljku „Mobitel i tablet".

Narančasti brojevi u krugovima, oznake „STATE 1", „LOGIN · A" i blokovi „What changed" u PDF-ovima
su anotacije dizajna i namjerno nisu ugrađeni.

## Okvir aplikacije (sidebar, top bar)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| „karlo's homelab" kao ime instance | Zadano se izvodi iz imena prvog admina; mijenja se u Admin → General | – | – |
| „Owner" ispod imena korisnika | „Owner" se prikazuje svakom adminu, „Member" svima ostalima | Ne postoji uloga vlasnika | #106 |
| Verzija `v0.9.2` | Konstanta u `src/backend/config.py` i druga u `src/collector/collector.py` | Nema jednog izvora verzije pri buildu | #106 |
| Prečac `⌘K` | Na Linuxu i Windowsu piše `Ctrl K` | Prikazuje se prečac koji stvarno radi na tom sustavu | – |

## Overview (`01-dashboard.pdf`, `06-dashboard-empty-states.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Agentova bilješka na svakom incidentu („Agent: limit is 4 GB…") | Bilješka dolazi iz lokalne dijagnoze po pravilima, ne od jezičnog modela | Automatska LLM dijagnoza ne postoji | #93 |
| „Ask the agent" otvara razgovor o tom kontejneru | Vodi na `/agent` bez konteksta kontejnera („Open diagnosis" otvara točno taj incident) | Chat još ne prima kontekst kontejnera | – |
| Zadnji incident: „Resolved 2 h ago · memory limit raised to 6 GB" | Piše samo kada je riješen i na kojem serveru | Nitko ne upisuje kako je incident riješen | – |
| „You'll get a push on ntfy if that changes" | Rečenica je izostavljena | Obavijesti ovise o kanalima koje je svaki korisnik postavio, pa rečenica ne bi vrijedila za svakoga | – |
| Gumbi Logs i Restart u proširenom redu kontejnera | Prikazani, ali onemogućeni, s oznakom „Coming soon" i objašnjenjem u tooltipu | Dogovoreno: aplikacija ostaje read-only dok se to sigurnosno ne pregleda | #100 |
| „Monitor this machine": „Check every 15 s · change later in Admin" | „Checks every minute at first · change it once it reports" | Novi server počinje s intervalom od 60 s; interval se mijenja na Overviewu ili u Adminu | – |
| „Another machine": jedna `docker run` naredba | Dvije `docker run` naredbe (collector i docker-proxy) | Collector namjerno nema Docker socket; drži ga zaseban read-only proxy | – |
| „Another machine": polje samo za ime | Dodano je i polje za adresu na koju se novi stroj javlja | Bez ispravne adrese naredba ne radi (localhost, VPN) | – |
| Brisanje servera nije na Overviewu | „Remove this server" je i u System tabu, uz Admin → Servers | Zgodno je imati ga uz podatke servera | – |
| Kartica „What you'll get": „Kept for a year as 30-minute points" | „Kept as rolled-up points for as long as you set in Admin" | Zadržavanje je postavka, a točke nisu uvijek 30-minutne | – |
| „The agent only sends data to your model provider when it diagnoses an incident" | „…when you ask it something" | Automatska dijagnoza ne postoji | #93 |

## Tabovi servera (`08-server-tabs.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Containers: „Restarts · 24 h" | „Restarts · since created" | Postoji samo Dockerov ukupni brojač | #105 |
| Network: imena host procesa (`sshd`, `vite dev server`) | Ime se vidi kad port objavljuje kontejner; host procesi samo uz opcionalni `port-owners` helper; bez njega `sshd` je pretpostavka po portu 22 | Čitanje tuđih procesa traži velike privilegije, pa je isključeno po defaultu | – |
| Network: „Bound to" s pravom adresom (`10.0.0.12`) | `0.0.0.0`, `127.0.0.1` ili „one address" | Collector šalje samo opseg (svi, loopback, jedna adresa) | #103 |
| Network: „Reachable from: LAN + VPN" | „every network", „one network" ili „this machine" | Isto kao gore | #103 |
| Network: sučelja s opisom („WireGuard", „9 bridges") | Ime, mreža, brzina i promet | Vrsta sučelja se ne prikuplja | #39 |
| System: „Security: 4 · openssl, libc6, sudo…" | Samo broj („4 waiting") | Imena paketa nisu ni u jednoj datoteci koju collector smije čitati | #104 |
| System: ažuriranja za sve distribucije | Samo Debian i Ubuntu (apt) | dnf i pacman nemaju čitač | #104 |
| System: „Failed units: 0 systemd units" | Red je izostavljen | Jedini put je D-Bus, koji može i mijenjati servise | #102 |
| System: „Disk errors: None · SMART healthy" | Brojač I/O grešaka iz kernela; NVMe ga nema | SMART traži sirov pristup diskovima | #101 |
| System: stupac Health po disku („SMART ok · 3% worn") | Stupac Inodes | Isto kao gore | #101 |
| System: „Last shutdown: Clean, by reboot command" | „Clean" ili „Unclean" | Uzrok gašenja se ne bilježi | – |
| System: „Proxy: docker-socket-proxy" | „Docker socket proxy, read-only" | Proxy je vlastiti, ne projekt tog imena | – |
| System: `/var/lib/docker` s uređajem i tipom `overlay2` | Uređaj „—", tip „images + cache" | `/system/df` je namjerno sveden na šest brojeva | – |
| Events: „Events are kept for 90 days, then only alerts are kept for a year" | „Events are kept for N days" (postavka zadržavanja) | Ne postoji zasebno pravilo za alerte | #58 |
| Events: „watchtower pulled new images…", „restarted by a user through Portainer" | Generičke rečenice („now run a different image build") | Infradeck ne zna tko je pokrenuo promjenu | – |
| Events: „alert sent to ntfy and email" | Ne piše kamo je alert poslan | Ne bilježi se slanje po kanalu | – |

## Prijava i registracija (`09-login.pdf`, `mobile/06-login.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| „Forgot it?" vodi na reset lozinke | Otvara objašnjenje: pitaj admina ili napravi novog admina sa servera | Reset lozinke ne postoji; Infradeck ne šalje e-mail | #108 |
| Stanje greške nije nacrtano | Tihi žuti okvir iznad gumba | Dizajn ga ne prikazuje | – |
| `/register` uz zatvorenu registraciju nije nacrtan | Kratka poruka s poveznicom na prijavu | Dizajn ga ne prikazuje | – |
| Mobitel, 2FA: sustavska tipkovnica i „From Messages" | Polja traže numeričku tipkovnicu i `one-time-code`; što se prikaže ovisi o uređaju | To crta operativni sustav, ne stranica | – |
| 30 dana ostaje nakon promjene lozinke | Sesija se nakon promjene lozinke ili uključivanja 2FA vraća na jedan dan | Te radnje otvaraju novu sesiju bez oznake „remember" | – |

## Statistics (`02-statistics.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Birač servera: samo serveri | Dodana je stavka „All" s prikazom flote | Dogovoreno, da se ne izgube incidenti i dostupnost | – |
| Prikaz flote nije nacrtan | Stari sadržaj (incidenti, dostupnost, rang-liste) u novim bojama; krafne i stupci zadržavaju zelenu, žutu i narančastu | Za taj prikaz nema dizajna | – |
| Events red: „nightly backup" | Oznaka „nightly <kontejner> peak" na svakom skoku koji se ponavlja | Infradeck ne zna što je backup; zna samo koji je kontejner tada najzaposleniji | – |
| Events red: oznake uz svaki događaj | Tekst se prikazuje samo gdje ima mjesta do sljedećeg događaja; ostalo je u tooltipu | Inače se oznake preklapaju | – |
| Events red: „apt upgrade · 41 packages" | Isto, ali tek od trenutka kad collector to počne javljati; samo Debian i Ubuntu | Događaj nastaje kad se apt-ov log promijeni između dva javljanja | #104 |
| Nalaz: „line up with the gitlab backup job" | „gitlab is the busiest container each time" | Isto kao gore | – |
| Nalaz: „Spikes last 8 to 14 minutes" | Isto, ali samo za raspone do 7 dana | Trajanje se čita iz pojedinačnih javljanja, a ona se čuvaju tjedan dana | #58 |
| Tablica: Swap po kontejneru | Prosjek kroz raspon; samo cgroup v2 i tek nakon rebuilda collectora; za razdoblja prije toga prazno | Docker ne daje swap po kontejneru, čita se iz cgroupa | – |
| Tooltip: „Busiest gitlab 94%" | Najviši CPU jednog kontejnera u tom razdoblju; za raspone od 30 dana naviše po satu, tjednu ili mjesecu | Čita se iz sažetaka | – |
| „Not measured before collector 0.9.0" | „Not measured before <datum>" | Povijest verzija collectora se ne bilježi | – |
| Raspon boja u heatmapu „0% – 15%+" | Gornja granica se računa iz podataka | Fiksna granica ne odgovara svakom serveru | – |
| Export CSV | Izvozi točke Timelinea odabranog servera i raspona | Dizajn ne kaže što se izvozi | #67 |
| Povijest kontejnera nije nacrtana | Klik na red otvara Timeline za taj kontejner (CPU, Memory, Restart count) ispod reda | Dogovoreno | – |

## Agent (`03-agent.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Dijagnozu piše jezični model („watchtower updated the image to 17.4.1…") | Dijagnoza se gradi fiksnim pravilima iz spremljenih metrika i događaja; sažetak je opis pravila | Automatska LLM dijagnoza ne postoji; ovako ne košta ništa i ništa ne šalje van | #93 |
| Evidence: retci iz loga kontejnera i `docker events` | Događaji koje je Infradeck zabilježio na tom serveru | Docker proxy ne može čitati logove | #100 |
| „Confidence: high · 3 of 3 signals agree" | „3 of 3 checks against the data hold" | Nema modela koji bi procijenio pouzdanost | #93 |
| Koraci s točnom putanjom (`/opt/gitlab/docker-compose.yml`) i brojkama iz povijesti verzija | Koraci iz fiksnog priručnika po vrsti pravila, s imenom kontejnera i izračunatim limitom | Infradeck ne zna gdje je compose datoteka ni koju verziju slike je kontejner imao prije | – |
| „Notified ntfy, email" | „sent to the channels people chose" ili „nobody yet" | Ne bilježi se po kanalu | – |
| Chat: odgovori s oznakama izvora („memory 12:30–13:55", „docker events") | Običan tekst odgovora | Model ne vraća izvore | #93 |
| Chat: prijedlozi pitanja vezani uz incident | Tri općenita prijedloga | Isto | – |
| „Resolved 14" po tjednima, s opisom kako je riješeno | Zadnjih 200 riješenih; opis je „Marked resolved by …" ili „Cleared on its own" | Nitko ne upisuje kako je riješeno | – |
| „Mark resolved" trajno zatvara | Ako je uzrok još tu, na sljedećem javljanju otvara se novi incident | Pravilo i dalje vrijedi | – |
| Viewer ne može ništa mijenjati | Isto; gumbi su onemogućeni s objašnjenjem | – | – |

## Account (`04-account.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Email „Verified" | Bez oznake | Infradeck ne šalje e-mail, pa nema provjere | – |
| Recovery codes: „Show" | Samo „Regenerate" | Kodovi su spremljeni kao hash | – |
| Recovery codes: „8 of 10 left · generated 25 Sep" | „8 left" | Datum generiranja se ne bilježi | – |
| Passkeys: „Add passkey" | Gumb je onemogućen, s oznakom „Coming soon" | WebAuthn nije napravljen | #77 |
| Sessions: lokacija („Zagreb, HR") i „Last active" | IP adresa i vrijeme prijave | Nema geolokacije; zadnja aktivnost se ne bilježi | – |
| „Sessions expire after 30 days without activity" | „A session lasts a day, or 30 days…" | Sesija istječe od prijave, ne od zadnje aktivnosti | – |
| Kanali: Telegram, Slack, Gotify, Pushover | ntfy, Discord webhook, bilo koji webhook, e-mail (ako je SMTP postavljen) | Ostali idu kroz generički webhook | #50 |
| Kanal: „Fix" kod neuspjeha | „Send test" i „Remove" | Adresa se ne može urediti jer je spremljena šifrirano; kanal se ukloni i doda ponovno | – |
| Matrica: „Agent diagnosis ready", „Daily digest 08:00", „Weekly report" | Redovi su prikazani, ali onemogućeni | Ne postoje | #33 |
| Display: Time format i Sizes mijenjaju prikaz | Spremaju se, ali se još ne primjenjuju | Vremena i veličine se formatiraju na puno mjesta | – |
| Display: Time zone za grafove | Vrijedi samo za tihe sate; grafovi prate preglednik | Isto | – |
| „Change initials color" | Mijenja boju kroz šest ponuđenih | Dizajn ne kaže kako | – |

## Admin (`05-admin.pdf`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| „Update available · 0.9.3" | Nema; About ima poveznice na changelog | Infradeck ne zove van | – |
| Connect a server kao tri numerirana koraka | Iste kartice kao na Overviewu (ime, naredba, čekanje, Add) | Jedna implementacija umjesto dvije | – |
| „Removing a server keeps its history" | Brisanje servera briše i njegove metrike i incidente | Tako radi postojeće brisanje | – |
| Users: „Invite pending · Resend" | Poveznica se prikaže jednom; „Cancel" i nova pozivnica | Infradeck ne šalje e-mail | – |
| Uloge Owner / Admin / Viewer | Owner je prvi admin i ne može se mijenjati; ostalo Admin ili Viewer | Nema zasebne uloge vlasnika | #106 |
| Alert rules: „Disk forecast full in < 30 days", „CPU > 90%" | Ne postoje; popis ima 20 stvarnih pravila | Ta pravila nisu napravljena | – |
| Alert rules: stupac Servers s izborom servera | Uvijek „All" | Pravila vrijede za sve servere | – |
| Alert rules: „New rule" | Gumb je onemogućen, s oznakom „Coming soon" | Vlastita pravila ne postoje | – |
| Alert rules: bilješka zašto je pravilo isključeno | Samo broj isključenih pravila | Razlog se ne upisuje | – |
| Incident agent: „Anthropic API · key ending 4f2a", „Change" | OpenAI, ključ i model iz `.env`; „Change" je onemogućen, „Test connection" provjerava ključ | Ključ se zasad postavlja u `.env`; spremanje u Adminu je dio #109 | #109 |
| „Diagnose new incidents automatically" prekidač | Uvijek uključeno i onemogućeno | Dijagnoza je lokalna i ne može se isključiti | #93 |
| „Read container logs" s iznimkama | Onemogućeno | Proxy ne čita logove | #100 |
| „Redact IPs and hostnames" | Onemogućeno | Nije napravljeno | – |
| Incident agent: nema retka za broj pretraga | Redak „Look-ups": koliko alata agent smije pozvati po dijagnozi (8) i po poruci u chatu (4) | Budžet poziva alata, podizanje s 0 traži lozinku i 2FA | #157 |
| „$1.84 estimated provider cost · cap $10" | „— · not tracked" | Potrošnja se ne mjeri | #91 |
| Retention: tri zasebna roka | Jedan rok za sažetke; javljanja se čuvaju tjedan dana | Tako radi postojeće zadržavanje | #58 |
| Database: putanja, rast po tjednu, „Last backup", „Export", „Back up now" | Veličina baze i broj zapisa; gumbi su onemogućeni, s oznakom „Coming soon" | Backup ne postoji | – |
| About: „built 2 Oct 2026" | Samo verzija | Datum builda se ne bilježi | #106 |

## Mobitel i tablet (`design/mobile/`)

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Overview: ploča „Agent" s bilješkom nije na mobitelu | Kartica incidenta nema agentovu bilješku | Dizajn je na mobitelu ne prikazuje; bilješka je na ekranu incidenta | – |
| Server: graf „Disk" uz CPU, Memory i Load | Disk crta najpuniji disk iz povijesti diska (1 h ili 6 h), ne iz živih uzoraka | Živi uzorci nemaju disk | – |
| Server: tabovi Network, System i Events nisu nacrtani za mobitel | Desktopni sadržaj u jednom stupcu; tablica portova postaje kartice, tablica diskova se pomiče vodoravno | Za te tabove nema mobilnog dizajna | – |
| Kontejner u sheetu: gumbi Logs i Restart | Prikazani, ali onemogućeni, s oznakom „Coming soon" | Isto kao na desktopu | #100 |
| Statistics: Timeline je zaseban ekran, ulaz nije nacrtan | Na glavnom ekranu je kartica „Timeline" s gumbom „Open timeline"; „Show on timeline" otvara ekran na tom trenutku | Dizajn ne kaže kako se dolazi do ekrana B | – |
| Statistics: „Time spent at each level" nije na mobitelu | Prikazano ispod „When it's busy" | Dizajn kaže „Same data as desktop" | – |
| Statistics: legenda „This week / Last week" | „Last 7 days / The previous 7 days" | Isti tekst kao na desktopu | – |
| Agent: „Show 5 log lines" | „Show N recorded events" | Infradeck ne čita logove kontejnera, nego bilježi događaje | #100 |
| Agent: „Push alerts are on · Critical only between 23:00 and 07:00" | Redak „Where alerts go" vodi u Account | Infradeck nema push obavijesti na mobitel (nema aplikacije ni Web Pusha) | #50 |
| Agent chat: oznake izvora ispod odgovora | Običan tekst | Model ne vraća izvore | #93 |
| More: „Owner" | Owner za admine, Member za ostale | Ne postoji uloga vlasnika | #106 |
| Security: redovi s strelicom i prekidačem za 2FA | Isti redovi kao na desktopu (gumbi „Change password", „Set up") | Postupci traže lozinku i kod, pa ostaju gumbi | – |
| Sessions: lokacija i „via WireGuard" | Adresa i vrijeme prijave | Nema geolokacije | – |
| Notifications: kanal „This iPhone · Push" | Nema | Nema push obavijesti | #50 |
| Notifications: redovi „Weekly report" i „Email digest 08:00" | Jedna rečenica da još ne postoje | Ne postoje | #33 |
| Admin: „Infradeck 0.9.3 is out" | Nema; hub prikazuje strojeve koji čekaju | Infradeck ne zove van | – |
| Admin: „Incident agent · $1.84 of $10" | „chat on/off" ili „no key" | Potrošnja se ne mjeri | #91 |
| Admin: Connect a server u tri numerirana koraka | Ista kartica kao na desktopu, uz Copy i Share ispod naredbe | Jedna implementacija | – |
| Admin: „You'll get a push when it connects" | Nema | Nema push obavijesti | #50 |
| Alert rules: „+" za novo pravilo | Nema | Vlastita pravila ne postoje | – |
| Tablet: Agent skriva chat iza gumba | Na tabletu su popis, incident i chat jedan ispod drugoga | Tri stupca ostaju od 1200 px; ispod toga slažu se okomito | – |

## Availability (`10-availability.pdf`, `mobile/07-availability.pdf`)

Prvi dio milestonea: provjere HTTP(s), Keyword i TCP port, pokreću ih collectori od verzije 0.10.0.

| Dizajn | Implementacija | Razlog | Issue |
| --- | --- | --- | --- |
| Gumbi „Status page" i „Maintenance" | Prikazani, ali onemogućeni, s oznakom „Coming soon" | Još nisu napravljeni | #143, #144 |
| Devet vrsta provjera | Rade HTTP(s), Keyword, JSON query, TCP port, TLS certificate, DNS i Ping; Docker container i Push prikazani kao „later" i ne mogu se odabrati | Preostala dva dijela | #135, #136 |
| JSON query: JSONata izrazi (kao Uptime Kuma) | Jednostavna staza (`data.status`, `items[0].ok`) + usporedba (equals / contains / > / < / exists) | JSONata traži vanjsku ovisnost i izvršavanje izraza nad odgovorom (DoS/sigurnost); staza je čisto navigiranje | #133 |
| TLS certificate: čita istek i za samopotpisane certifikate | Lanac certifikata mora biti valjan; za samopotpisane ide TCP ili HTTP provjera uz „Ignore TLS errors" | Čitanje isteka bez validacije traži ručni ASN.1 parser; izbjegnuto | #133 |
| DNS: usporedba odgovora po zapisu i prioritetu | Očekivani odgovor je točna podudarnost (TXT: sadrži) bilo kojeg zapisa; vlastiti UDP klijent (A, AAAA, CNAME, MX, TXT, NS) | Dovoljno za homelab; bez vanjske DNS biblioteke | #133 |
| Ping | ICMP preko unprivileged datagram socketa; collector treba `net.ipv4.ping_group_range` (dodan u compose). Bez njega provjera javi da ICMP nije dopušten | Zatvoreni container (`cap_drop: ALL`) ne daje raw sockete; ovo je najmanja ovlast, bez `NET_RAW` | #133 |
| Tab „Find on this machine" u drawer-u New check | Samo ručno dodavanje | Prvi dio | #142 |
| Grupe po tagu redom kako ih je korisnik složio (Public prvi) | Abecedno, provjere bez taga na kraju | Nema spremljenog redoslijeda grupa | – |
| Detalj: izdavatelj certifikata („Let's Encrypt R11") | Samo broj dana do isteka | Collector šalje samo datum isteka | #140 |
| Detalj: graf za 30 d po serveru | 30 d je jedna linija za sve servere zajedno | Satni sažeci nisu po serveru; sirovi rezultati čuvaju se 7 dana | #140 |
| Detalj: događaj „Paused 20 min for maintenance" | Događaji su samo padovi i oporavci (iz incidenata) | Pauze i održavanje se još ne bilježe | #143 |
| Detalj: gumb „Clone" | Pause, Edit i Delete | Prvi dio | #140 |
| Down now: „alert sent to ntfy" i sažetak agenta | Razlog pada, s kojih servera i koliko puta zaredom; za novu provjeru „no alert yet" | Ne bilježi se slanje po kanalu; agent za provjere još ne postoji | #137, #145 |
| Down now: „Pause alerts" | „Pause" pauzira cijelu provjeru | Odvojeno pauziranje obavijesti nije napravljeno | #137 |
| New check: obavijesti po kanalu, „While down, remind every", „Alert on certificate expiry" | Pad otvara incident koji ide na iste kanale kao svi incidenti; bira se samo Critical/Warning | Obavijesti idu kroz postojeće incidente | #137 |
| New check: „Test passed · 200 OK · 142 ms from both servers" odmah | Test traje 5–15 s: collectori pitaju za posao svakih 10 s kad nemaju što javiti; nova provjera i dalje prvih 10 minuta ne šalje obavijesti | Collector ne prima veze izvana, nego sam pita dashboard | #141 |
| New check: timeout fiksno 48 s | Timeout se sam smanji na 80 % intervala kad je interval kraći; greške se pokazuju uz polje i u podnožju | Zadani timeout inače blokira interval ispod 49 s | #141 |
| New check: tagovi kao čipovi | Tekstno polje, tagovi odvojeni zarezom | Jednostavnije | #141 |
| Mobitel: Availability kao zaseban tab | Availability je u More (s brojem palih provjera na tabu More); popis provjera je kompaktan, s trakom zadnjih 30 provjera | Tab bar ima mjesta za četiri stavke | #146 |
| Agent confirm (`11-agent-confirm.pdf`): dizajn pokazuje samo „Diagnose new incidents automatically" | Isti popup pita i za Chat (Off → On), Chat access (Admins only → Per user, gumb „Verify and save") i spremanje providera (npr. OpenAI → Anthropic ili stari → novi model, gumb „Verify and save") | Sve te promjene šalju podatke vanjskom modelu, pa traže istu potvrdu | #184 |
| Agent confirm: admin bez 2FA | Umjesto polja za lozinku popup kaže da prvo treba uključiti 2FA, s poveznicom na Account › Security | Dizajn ne pokazuje to stanje; bez 2FA promjena nije moguća | #184 |
| Agent confirm: popup „centred on the content" | Popup je centriran na cijeli ekran, a ne na sadržaj desno od bočne trake | Karlova odluka nakon pregleda (7. 11. 2026) | #184 |
