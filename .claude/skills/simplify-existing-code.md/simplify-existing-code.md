---
name: simplify-existing-code
description: Koristi kad treba pojednostaviti postojeći kod koji je prekomplikiran: ukloniti nepotrebne apstrakcije, slojeve, nekorištene parametre ili mrtvi kod, a da se ponašanje ne promijeni. Ne koristi za pisanje novog koda (code-simplicity) ni za male ciljane popravke (minimal-diff).
---

# Pojednostavljenje postojećeg koda

Cilj je manje koda uz isto ponašanje. Prije nego što išta mijenjaš, moraš razumjeti što kod radi.

## Postupak

1. **Utvrdi ponašanje.** Pročitaj kod i kratko opiši što radi, uključujući rubne slučajeve. Ako postoje testovi, pokreni ih prije izmjena i zabilježi rezultat.
2. **Traži kandidate za uklanjanje:** apstraktne klase s jednom implementacijom, wrapper funkcije koje samo prosljeđuju poziv, parametri koji se uvijek dobivaju istu vrijednost, grane koje se nikad ne izvršavaju, nekorištene funkcije (provjeri pretragom po projektu prije brisanja).
3. **Mijenjaj u malim koracima.** Jedna vrsta pojednostavljenja po koraku, nakon čega provjeri testove ili ponašanje.
4. **Ne diraj javna sučelja** koja koriste drugi dijelovi sustava, osim ako je to dio dogovorenog zadatka.
5. **Sačuvaj prijašnje ponašanje u iznimkama i rubnim slučajevima.** Pojednostavljenje ne smije tiho promijeniti poruke grešaka, redoslijed obrade ili tipove povratnih vrijednosti.

## Što ne raditi

- Ne prepisuj kod u "savršeniji" obrazac ako to nije potrebno za smanjenje složenosti.
- Ne uklanjaj obradu grešaka za ulaze izvana, čak i ako izgleda suvišno.
- Ne mijenjaj ponašanje kako bi kod bio kraći.

## Izvještaj

Na kraju ukratko navedi: što si uklonio ili pojednostavio, je li ponašanje ostalo isto (i kako si to provjerio) te što si primijetio, ali nisi dirao.