---
name: security-audit
description: Detaljan sigurnosni pregled (security review / audit) cijelog koda ili dijela koda, uz dojavu pronađenih propusta s ozbiljnošću, lokacijom, scenarijem napada i popravkom. Use when the user asks for a security review, security audit, vulnerability hunt, pentest-style code review, "provjeri sigurnost", "sigurnosni pregled", "ima li propusta", or wants a component (backend, collector, docker proxy, nginx, compose, CI, frontend, assistant) reviewed for security. For a quick check of only the pending diff, the built-in security-review skill is enough.
argument-hint: "[full | backend | collector | infra | frontend | assistant | <path> | diff]"
---

# Security audit

Cilj: pronaći **stvarne, iskoristive** sigurnosne propuste u ovom repozitoriju i dojaviti ih
tako da ih vlasnik može odmah popraviti. Projekt je open-source, self-hosted alat koji ima
pristup Docker socketu i hostu, pa vlasnik želi **strogu** procjenu: ne ublažavaj rizik
radi udobnosti, ne zaokružuj "vjerojatno OK" na "OK".

Ovaj skill **ne mijenja kod** i ne commitira. Samo pregledava i izvještava. Popravke radiš
tek kad korisnik to izričito zatraži.

## Argumenti

`$ARGUMENTS` određuje opseg. Bez argumenta = `full`.

| Argument | Opseg |
|---|---|
| `full` | sve dolje, redom |
| `backend` | `src/backend/**` |
| `collector` | `src/collector/**` (collector, docker_proxy, system_helper) |
| `infra` | `docker/**`, `*compose*.yml`, `example.env`, `.github/**`, `.gitignore`, `.dockerignore` |
| `frontend` | `src/frontend/**` |
| `assistant` | Incident agent: `src/agent/**` (providers, tools, investigate, diagnosis, chat, masking, safety, jobs, worker, usage), `SYSTEM_PROMPT.md`, `DIAGNOSIS_PROMPT.md`, konverzacije u backendu |
| `<path>` | taj put (datoteka ili direktorij) plus ono što ga poziva |
| `diff` | samo neprihvaćene promjene (`git diff` + `git diff --staged`) pročitane u kontekstu cijelih datoteka |

Ako je argument dvosmislen, odaberi najbliži opseg i na početku izvještaja reci koji si odabrao.

## Pravila rada (čitaj prije početka)

1. **Čitaj datoteke cijele.** `grep` služi za pronalaženje kandidata, nikad za oslobađanje
   koda od sumnje. Funkcija koja izgleda sigurno često ima rupu u pozivatelju ili u redoslijedu
   provjera. `src/backend/main.py` ima ~1800 redaka; pročitaj ga u cijelosti, u dijelovima.
2. **Ne ispisuj tajne.** `.env` postoji lokalno. Ako ga treba otvoriti, ispiši samo *imena*
   varijabli i je li vrijednost prazna/zadana, nikad vrijednosti. Isto vrijedi za tokene,
   ključeve i lozinke pronađene u kodu ili povijesti: u izvještaju navedi lokaciju i vrstu,
   ne sadržaj (najviše prva 4 znaka).
3. **Samo lokalno i neškodljivo.** Ne šalji zahtjeve prema stvarnim sustavima, ne pokreći
   Docker kontejnere ni skeniranja. Za potvrdu propusta smiješ pokrenuti postojeće testove
   (`pytest`) i napisati kratku skriptu u scratchpad direktoriju koja poziva funkcije
   izravno s neprijateljskim ulazom. Ništa što pokreneš ne smije dirati stvarnu bazu ili
   mrežu.
4. **Provjeri tvrdnje iz dokumentacije.** `SECURITY.md` i `docs/` tvrde mnogo ("rate limited",
   "single-use", "capped", "never follows symlinks"). Za svaku tvrdnju u svom opsegu nađi kod
   koji to provodi i provjeri da vrijedi na *svim* putevima. Tvrdnja bez pokrića u kodu je
   nalaz (dokumentirana kontrola ne postoji), a kontrola koja postoji samo na jednom putu
   također.
5. **Razmišljaj kao napadač, ne kao lintar.** Za svaki ulaz pitaj: tko ga može poslati, što je
   najgore što može sadržavati, što sustav radi s tim i što je najgore što se zbog toga može
   dogoditi. Profile napadača vidi u [references/threat-model.md](references/threat-model.md).
6. **Prvo provjeri je li nešto već riješeno.** Prije nego prijaviš nalaz, nađi postojeću
   kontrolu (middleware, dependency, nginx pravilo, test) i provjeri zaobilazi li se. Nalaz
   koji ignorira postojeću zaštitu je šum i troši korisnikovo povjerenje.
7. **Svaki nalaz verificiraj** (vidi niže) i razvrstaj kao `POTVRĐENO` ili `TREBA PROVJERU`.
   Ne prijavljuj ono što nisi mogao provjeriti kao potvrđeno.

## Postupak

### 1. Priprema (uvijek)

- Pročitaj [references/threat-model.md](references/threat-model.md) i `SECURITY.md`.
- `git status` i `git log --oneline -15`: što se nedavno mijenjalo, nove površine napada
  najčešće su u svježem kodu.
- Za `backend`/`full`: izlistaj sve rute (`grep -nE "^@(app|router)\.(get|post|put|patch|delete)" src/backend/main.py`)
  i složi **inventar ruta**: metoda, put, tko smije (anonimno / bilo koji korisnik / admin /
  kolektor s tokenom), je li iza CSRF/Origin provjere, ima li rate limit, što čita/piše.
  Ruta bez jasnog odgovora u bilo kojem stupcu je kandidat za nalaz.
- Pročitaj popis ovisnosti (`requirements*.txt`, `src/frontend/package.json`) radi kasnije
  provjere.

### 2. Pregled po područjima

Za svako područje u opsegu pročitaj odgovarajuću kontrolnu listu i prođi je **stavku po stavku**
nad stvarnim kodom:

| Područje | Kontrolna lista |
|---|---|
| Backend (API, auth, sesije, 2FA, baza, pravila, alarmi) | [references/checklist-backend.md](references/checklist-backend.md) |
| Collector, docker proxy, system helper, LLM asistent | [references/checklist-collector-agent.md](references/checklist-collector-agent.md) |
| Docker, compose, nginx, Postgres, CI, repozitorij, ovisnosti | [references/checklist-infra.md](references/checklist-infra.md) |
| Frontend | [references/checklist-frontend.md](references/checklist-frontend.md) |

Kontrolne liste su podsjetnici, **ne granica**. Ako vidiš nešto sumnjivo što nije na listi,
istraži i to.

**Paralelizacija za `full`:** opseg je velik. Ako ti je dostupan alat `Agent`, pokreni četiri
agenta paralelno (po jedno područje iz tablice), svakome daj samostalan prompt iz predloška
niže, a zatim sam spoji, deduplikaj i **ponovno verificiraj** sve što su vratili prije
izvještaja. Nalaz koji agent prijavi nije potvrđen dok ga ti nisi pročitao u kodu. Ako ne
možeš koristiti agente, prođi područja redom sam.

Predložak za agenta:

> Radiš sigurnosni pregled (samo čitanje, ništa ne mijenjaj, ne commitaj, ne ispisuj vrijednosti
> tajni) područja **<područje>** u repozitoriju <putanja>. Prvo pročitaj
> `.claude/skills/security-audit/references/threat-model.md` i
> `.claude/skills/security-audit/references/<checklist>.md`, zatim pročitaj **cijele** datoteke
> tog područja i prođi kontrolnu listu stavku po stavku. Za svaki nalaz vrati: naslov,
> datoteka:redak, što je pogrešno, konkretan scenarij napada (tko, što pošalje, što dobije),
> koju postojeću zaštitu si provjerio i zašto ne pomaže, predloženi popravak i svoju sigurnost
> (visoka/srednja/niska). Navedi i koje datoteke si pročitao, a koje nisi. Ne prijavljuj
> teorijske probleme bez puta do iskorištenja.

### 3. Verifikacija svakog nalaza

Za svaki kandidat napiši (barem u glavi, a u izvještaju sažeto):

1. **Izvor:** tko kontrolira ulaz i na kojem je povjerenju (anonimni, korisnik, admin,
   kolektor, VPN peer, podatak s hosta)?
2. **Put:** kojim funkcijama ulaz stiže do opasnog mjesta? Navedi datoteke i retke.
3. **Zaštite na putu:** koje provjere, validacije, limiti, nginx pravila postoje? Zašto ne
   pomažu (ili zašto pomažu, pa nalaza nema)?
4. **Učinak:** što napadač dobiva (čitanje podataka, promjena stanja, preuzimanje računa,
   izvršavanje koda, DoS, eskalacija na host)?
5. **Dokaz:** gdje je moguće, potvrdi testom ili kratkom lokalnom skriptom. Ako ne možeš,
   nalaz je `TREBA PROVJERU` s opisom što točno treba provjeriti.

Odbaci kandidata ako: nema puta do iskorištenja bez prethodnog admin pristupa koji već daje
isti učinak, ako je zaštićen nečim što si provjerio, ili ako je čista stilska preporuka.
Takve stvari, ako vrijede, idu u dio "Očvršćivanje" kratkom stavkom, ne među nalaze.

### 4. Ozbiljnost

Ocjenjuj prema učinku **u ovom sustavu** (pristup hostu i Docker API-ju je najskuplja stvar
koju treba braniti), uz prednost strožoj ocjeni kad si u dvojbi između dvije razine:

| Razina | Značenje | Primjeri |
|---|---|---|
| **KRITIČNO** | Daljinsko, bez/gotovo bez autentikacije, vodi do preuzimanja dashboarda, izvršavanja koda ili izlaska na host | zaobilaženje autentikacije, preuzimanje prve admin registracije, RCE, pristup Docker socketu iz javne strane |
| **VISOKO** | Autentificirani ili uvjetovani napad s velikim učinkom | eskalacija korisnik→admin, zaobilaženje 2FA, IDOR nad tuđim podacima, SQL injection, stored XSS u admin kontekstu, kompromitirani kolektor utječe na dashboard ili host |
| **SREDNJE** | Stvarno iskoristivo, ograničen učinak ili traži poseban uvjet | CSRF na manje osjetljivoj akciji, rate limit koji se zaobilazi, curenje informacija, DoS jednim zahtjevom |
| **NISKO** | Slabost bez jasnog puta do štete sam za sebe | nedostajući header, enumeracija korisnika, preslab zadani parametar |
| **INFO** | Zapažanje ili očvršćivanje | kontrola koja radi, ali je krhka |

### 5. Izvještaj

Format i redoslijed: [references/report-format.md](references/report-format.md). Ukratko:
sažetak (broj nalaza po razini, najvažniji jedan redak), **jedna tablica svih nalaza sortirana
prema riziku od najvišeg prema najnižem** (KRITIČNO, VISOKO, SREDNJE, NISKO, INFO), detalji
nalaza istim redom, tvrdnje iz dokumentacije koje kod ne podržava, očvršćivanje i pokrivenost
(što je pregledano, što nije i zašto).

**Tablica je obavezna.** Nalaze nikad ne prikazuj samo kao tekst niti grupirane po područjima.
Rezultate paralelnih agenata spoji u jednu tablicu i sortiraj ih zajedno.

Datoteke referenciraj kao markdown linkove (`[main.py:412](src/backend/main.py#L412)`), da su
klikabilne u editoru.

Izvještaj ispiši u razgovoru. **Ne spremaj ga u datoteku** (npr. `docs/`) dok to korisnik ne
zatraži, jer može sadržavati neobjavljene ranjivosti.

Na kraju ponudi sljedeći korak jednom rečenicom (npr. popraviti nalaze od najtežeg prema
najlakšem, uz test za svaki) i **čekaj odgovor**.

## Što ovaj skill nije

- Nije dinamičko testiranje: ne skenira živi sustav, ne fuzzira, ne pokreće exploite.
- Nije zamjena za `pip-audit` / `npm audit` ili skener slika; ako su dostupni i korisnik
  odobri mrežu, pokreni ih, a ako nisu, to **eksplicitno navedi** u pokrivenosti kao
  nepregledano (ovisnosti se ne ocjenjuju "iz glave" jer verzije nisu pouzdan izvor).
- Ne potvrđuje da je sustav siguran. Izvještaj "nema nalaza" smije reći samo "u pregledanom
  opsegu nisam našao iskoristive propuste", uz popis onoga što je pregledano.
