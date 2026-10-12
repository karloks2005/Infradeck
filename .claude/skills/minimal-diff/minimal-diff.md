---
name: minimal-diff
description: Koristi kad treba izmijeniti postojeći kod, popraviti grešku ili dodati manju funkcionalnost u postojeću bazu. Ne koristi za pisanje novog koda od nule (za to je code-simplicity).
---

# Minimalne izmjene postojećeg koda

Mijenjaj samo ono što je potrebno za zadatak.

## Pravila

1. **Prvo pročitaj okolni kod** i prati njegov stil, nazive i obrasce, čak i ako bi ti drugačije izgledao.
2. **Ne preuređuj susjedni kod.** Bez preimenovanja, premještanja, reformatiranja ili "čišćenja" koje nije dio zadatka.
3. **Ne dodaji promjene koje nisu tražene**, uključujući popravke tuđih grešaka koje nisu u opsegu zadatka. Spomeni ih u odgovoru.
4. **Drži diff malim.** Ako izmjena zahtijeva više od očekivanog, zaustavi se i objasni zašto prije nastavka.
5. **Ne mijenjaj javna sučelja** (potpise funkcija, API odgovore, nazive polja) bez izričitog dopuštenja.
6. **Test koji pratiš uz izmjenu ide u postojeću datoteku u odgovarajućoj mapi `tests/`** (vidi `TESTS.md`, "Where the tests live"). Novu datoteku napravi samo ako nijedna ne odgovara, u mapi područja, nikad izravno u `tests/`.

## Provjera prije predaje

- Je li svaka promijenjena linija potrebna za zadatak?
- Je li stil u skladu s okolnim kodom?
- Ako sam nešto primijetio izvan opsega, jesam li to spomenuo, a ne promijenio?