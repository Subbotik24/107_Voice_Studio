# Řešení potíží

## Ollama není dostupná

Spusťte Ollama, ověřte `ollama list`, poté v **Nastavení → Lokální AI** zvolte
**Obnovit**, vyberte model a nastavení uložte. VOICE Studio používá pevnou místní
adresu `127.0.0.1:11434`.

## Model Ollama nepodporuje zvuk

Vyberte model, který v Ollama hlásí capability `audio`. Program modely Ollama
neinstaluje ani neaktualizuje a bez vašeho výběru nepřejde na Whisper.

## Ollama nevrátila přepis

Pokud program zobrazí `Ollama returned no transcript`, v **Nastavení →
Rozpoznávání** zvolte jazyk odpovídající nahrávce nebo `auto` a operaci
opakujte s krátkým zřetelným úsekem. Jestli model daný jazyk zvuku nepodporuje,
zvolte profil **Lokální Whisper**; VOICE Studio motor samo nepřepíná.

## Nahrávka pro Ollama je delší než 30 minut

Profil Ollama zastaví místní převod na hranici 30 minut. Pro delší záznam zvolte
**Lokální Whisper** nebo rozdělte pracovní kopii; původní soubor nepřepisujte.

## AI oprava se nezdařila

Pokud se přepis otevřel s cleanup warning, první audio přepis byl uložen a není
ztracen. Zkontrolujte Ollama a spusťte **AI úprava…** ručně nebo text opravte v
editoru.

## Model Whisper není nainstalován

Chyba platí jen pro profil **Lokální Whisper**. V **Modely** model importujte
nebo stáhněte, ověřte a jeho přesné ID uložte v nastavení.

## Soubor nelze otevřít

Zkontrolujte podporovanou příponu, zvukovou stopu, místní přehrání a limit
2 GiB / dvě hodiny. Neobvyklý codec převeďte do kopie WAV nebo MP3; původní
soubor nepřepisujte.

## Nastavení se neuchovalo

Použijte **Uložit**, ne **Zrušit**. Poškozený settings JSON vyžaduje kontrolu
všech karet a nové uložení validních hodnot. Po restartu musí zůstat stejný
profil a model.

## SmartScreen nebo další potíže

Test RC není digitálně podepsán; ověřte zdroj ZIP a SHA-256. Pro podporu
vytvořte redacted report:

```text
voice-studio diagnostics --export report.json
```

Nepřikládejte soukromý zvuk, přepis, API keys, databázi, backup ani úplné místní
cesty.

## Obnovení bylo přerušeno a historie vypadá prázdná

Pokud bylo obnovení zálohy přerušeno a vedle úložiště jsou adresáře
`*.restore-*` a `*.recovery-*`, spusťte VOICE Studio znovu. Program použije
journal obnovení, operaci deterministicky dokončí nebo vrátí zpět a výsledek
zobrazí ve stavovém řádku. Při varování o journalu nic ručně nemažte a vytvořte
redacted diagnostics report. Adresář `*.recovery-*` se automaticky nemaže;
odstraňte jej až po kontrole historie.
