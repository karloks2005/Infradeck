---
name: code-simplicity
description: Koristi kad treba napisati, dodati ili izmijeniti kod, a postoji rizik od prekomjerne složenosti: nepotrebnih apstrakcija, slojeva, konfiguracijskih opcija, dodatne "proširivosti", pretjerane obrade grešaka ili nezatraženih testova, logiranja i dokumentacije. Ne koristi za sigurnosne revizije (za to je security-audit).
---

# Pojednostavljenje koda

Cilj je najmanji kod koji ispravno rješava zadatak i lako se čita i mijenja. Kraći kod koji radi bolji je od dužeg koji je "robustniji", ali nepotrebno složen.

## Pravila

1. **Apstrakcije tek kad ih zadatak traži.** Ne uvodi apstraktne klase, sučelja, tvorničke funkcije, strategije ni slojeve za nešto što se koristi na jednom mjestu. Ponavljanje dvaju-triju redaka obično je bolje od prerane apstrakcije.
2. **Bez nezatraženih parametara i opcija.** Ne dodaji konfiguraciju, zastavice ni parametre za scenarije koje nitko nije spomenuo.
3. **Obrada grešaka samo za stvarne ulaze.** Validiraj podatke koji dolaze izvana (korisnik, datoteka, mreža, API). Ne brani se od stanja koja se u kontekstu zadatka ne mogu dogoditi.
4. **Bez dodataka koji nisu tražени.** Ne dodaji logiranje, testove, dokumentaciju ni README izmjene osim ako su tražene. Komentar samo tamo gdje nešto nije očito iz koda.
5. **Ne mijenjaj ono što nije dio zadatka.** Ako vidiš prostor za poboljšanje u susjednom kodu, spomeni ga u odgovoru, ali ga ne diraj.
6. **Složenija struktura zahtijeva objašnjenje.** Ako misliš da je potreban veći dizajn, prvo kratko objasni zašto i pitaj, pa tek onda piši.

## Provjera prije predaje

Prije nego predaš kod, pročitaj ga i pitaj se:
- Mogu li ukloniti sloj, klasu ili funkciju a da ponašanje ostane isto?
- Postoji li parametar, grana ili provjera koja ne odgovara nijednom stvarnom slučaju?
- Je li naziv dovoljno jasan da komentar nije potreban?

Ako je odgovor "da" na bilo koje od prva dva pitanja, skrati.

## Primjer

Zadatak: "Napiši funkciju koja vraća zbroj cijena u košarici."

Preopširno:
```python
class PriceCalculator:
    def __init__(self, strategy=None, currency="EUR", rounding=2):
        ...
    def calculate(self, items):
        ...
```

Dovoljno:
```python
def total(items):
    return sum(item.price for item in items)
```

## Izuzeci

Složenost je opravdana kad zadatak to izričito traži, kad kod ide u veći sustav s jasno definiranim sučeljima, ili kad postojeći kod u projektu već koristi određeni obrazac. U tom slučaju prati postojeći obrazac, a ne uvodi novi. Ako nisi siguran, pitaj korisnika.