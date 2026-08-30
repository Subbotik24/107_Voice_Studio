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

## Obnova katalogu modelů vyžaduje pozornost

Spusťte offline obnovu a zkontrolujte její JSON výsledek:

```text
voice-studio models reconcile
```

Neúplný adresář modelu zůstává na místě pro ruční kontrolu; program jej
automaticky nepřevezme, nepřesune ani nesmaže. Po kontrole lze unmanaged adresář
odstranit uživatelsky potvrzeným příkazem
`voice-studio models remove MODEL_ID --yes`. Poškozený `catalog.json` se přesune
do karantény s časovým názvem `catalog.json.corrupt-*` a katalog se obnoví;
poškozený manifest zůstává zachován a automaticky se nikdy nemaže. Položky
`blocked` v JSON uvádějí cestu a důvod vyžadující pozornost.

## Spravovaná nahrávka je hlášena jako chybějící

Znovu spusťte `voice-studio storage audit` a v `missing_records` najděte ID
přepisu a přesnou cestu. Audit je pouze pro čtení: odchylky modelů a exportů
jsou uvedeny ve vnořených objektech `model_catalog` a `exports`, nic se však
neopravuje ani nemaže. Export `canonical_stale` je pouze konzervativní kandidát
a automaticky se nikdy neodstraní.

Nejprve mimo VOICE Studio ověřte, že spravovaná kopie skutečně chybí. Pokud má
záznam zůstat v historii bez uchovaného zvuku, výslovně odpojte pouze chybějící
odkaz:

```text
voice-studio storage repair-missing TRANSCRIPT_ID --expected-path PATH --yes
```

Pokud očekávaná cesta nesouhlasí, soubor se znovu objevil nebo cesta není
bezpečná, příkaz změnu záznamu odmítne. Úspěšná oprava zvuk nemaže ani znovu
nevytváří, nemění text přepisu ani původní soubor uživatele. Odchylky katalogu
modelů se podle potřeby opravují samostatně příkazem
`voice-studio models reconcile`.

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
