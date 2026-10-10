# Format izvještaja

Piši na hrvatskom (korisnik tako komunicira), a tehničke pojmove, imena funkcija i kod ostavi
kako jesu. Bez uvoda i bez hvaljenja. Najvažnije prvo.

## Struktura

### 1. Sažetak

- Opseg koji je stvarno pregledan (jedna rečenica) i datum/commit (`git rev-parse --short HEAD`,
  napomena ako ima nepohranjenih promjena).
- Jedan redak s brojem nalaza po razini (npr. "2 visoka, 3 srednja, 4 niska"), uz koliko ih je `POTVRĐENO`, a koliko `TREBA PROVJERU`.
- Jedna rečenica o **najvažnijem** nalazu ili "nisam našao iskoristive propuste u pregledanom
  opsegu" ako ih nema.

### 2. Tablica nalaza (obavezna)

Svi nalazi **moraju** biti prikazani u jednoj tablici, **sortirani prema razini rizika, od
najvišeg prema najnižem**: KRITIČNO, VISOKO, SREDNJE, NISKO, INFO. Unutar iste razine
`POTVRĐENO` ide prije `TREBA PROVJERU`, a zatim po iskoristivosti (lakše iskoristivo prvo).
Ne grupiraj po područjima (backend, infra, ...) i ne prikazuj nalaze u prozi umjesto tablice.

| ID | Rizik | Nalaz | Lokacija | Napadač | Učinak | Status | Popravak (jedna rečenica) |
|---|---|---|---|---|---|---|---|
| S-01 | KRITIČNO | Kratak naslov | [main.py:123](src/backend/main.py#L123) | profil iz threat-modela | Pristup | POTVRĐENO | Što promijeniti |
| S-02 | VISOKO | ... | ... | ... | DoS | TREBA PROVJERU | ... |

Pravila za tablicu:

- Stupac "Učinak" kaže što napadač **stvarno dobiva**, da se odmah vidi vodi li nalaz do upada.
  Koristi samo ove vrijednosti, a za više njih odvoji ih zarezom (najteža prva):
  `Izvršavanje koda` (kod ili naredbe na backendu, hostu ili kolektoru), `Pristup` (preuzimanje
  računa, sesije, admin prava, zaobilaženje autentikacije ili 2FA), `Krađa podataka`
  (čitanje podataka ili tajni koje napadač ne smije vidjeti), `Integritet` (podmetanje, mijenjanje
  ili utišavanje podataka i alarma), `DoS` (rušenje ili usporavanje, zaključavanje računa),
  `Izviđanje` (otkriva postojanje računa, strukturu ili konfiguraciju bez izravne štete).
  Ocjenjuj učinak u najgorem realnom slučaju, a ne u idealnom za napadača; ako ovisi o uvjetu
  koji nisi provjerio, napiši ga u detaljima.

- Jedan redak po nalazu; ID-evi `S-01`, `S-02`, ... dodjeljuju se **nakon sortiranja**, pa
  redni broj odgovara poretku rizika.
- Stupac "Lokacija" sadrži klikabilne markdown linkove (najviše dva; ostale navedi u
  detaljima).
- Ćelije su kratke (jedan redak). Sve duže ide u detalje ispod.
- Ako nema nalaza neke razine, ne dodaj prazne retke za nju.
- Ista tablica (isti stupci, isti poredak) vrijedi i za rezultate paralelnih agenata nakon
  spajanja: izvještaj je jedna tablica, ne četiri.

### 3. Detalji nalaza

Isti redoslijed kao u tablici. Svaki nalaz ima ovaj oblik (bez dodatnih naslova):

```
### [VISOKO] S-02 · Kratak, konkretan naslov (POTVRĐENO)

**Gdje:** [datoteka.py:123](putanja/datoteka.py#L123), poziva ga [drugi.py:45](putanja/drugi.py#L45)
**Napadač:** tko (npr. prijavljeni korisnik bez admin prava / VPN peer / kompromitirani kolektor)

**Problem:** što je pogrešno, jednom-dvjema rečenicama.

**Scenarij:** korak po korak što napadač pošalje i što dobije.

**Zašto postojeće zaštite ne pomažu:** koje si kontrole provjerio i zašto ih ovo zaobilazi.

**Popravak:** konkretna promjena (i test koji je treba pokriti), s kratkim isječkom koda ako
pomaže.

**Dokaz:** test/skripta koju si pokrenuo i rezultat, ili "nije pokrenuto: <razlog>".
```

ID je isti kao u tablici nalaza.
Za `TREBA PROVJERU` umjesto "Dokaz" napiši **Što treba provjeriti** (točno što i kako).

### 4. Tvrdnje iz dokumentacije koje kod ne podržava

Tablica: tvrdnja (citat + mjesto u `SECURITY.md`/`docs/`) | što kod zapravo radi | datoteka:redak.
Izostavi odlomak ako nema razlika.

### 5. Očvršćivanje (nije ranjivost)

Kratke stavke (jedan redak) za stvari koje nisu iskoristive same za sebe, ali bi smanjile
rizik. Ne miješaj ih s nalazima.

### 6. Pokrivenost

- **Pregledano:** popis datoteka/područja koja si pročitao u cijelosti.
- **Nije pregledano i zašto:** (npr. ovisnosti bez audit alata, živa konfiguracija
  Traefika/OpenVPN-a izvan repozitorija, dinamičko testiranje).
- **Pretpostavke** o okruženju na koje se nalazi oslanjaju.

### 7. Sljedeći korak

Jedna rečenica: predloži redoslijed popravljanja i pitaj želi li korisnik da počneš. Ništa
ne mijenjaj dok ne odgovori.

## Pravila stila

- Nalazi su činjenice s dokazom; nagađanje označi kao `TREBA PROVJERU`.
- Ne duplicirati: isti uzrok na više mjesta = jedan nalaz s popisom lokacija.
- Ne pretjeruj s ozbiljnošću radi dojma, ali ni ne ublažavaj (korisnik izričito traži
  strogu procjenu). Kad dvoji, napiši obje razine i obrazloži jednom rečenicom.
- Ne ispisuj vrijednosti tajni; navedi samo lokaciju i vrstu.
- Bez emojija.
