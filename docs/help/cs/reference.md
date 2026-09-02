# Přehled obrazovek a nastavení

## Levé menu

| Položka | Účel |
|---|---|
| **Přehled** | Místní statistika celé historie a poslední záznamy. |
| **Studio** | Nahrávání, přepis a editor. |
| **Slovník** | Spravovaný terminologický slovník s importem/exportem. |
| **Historie** | Hledání a kombinované filtry záznamů; otevře záznam ve Studiu. |
| **Modely** | Správa pouze modelů Faster Whisper. |
| **Záloha** | Vytvoření, ověření a obnovení zálohy. |
| **Nastavení** | Centrální stránka: profily, jazyky, Ollama, Whisper a OpenAI. |
| **Nápověda** | Centrální stránka s manuálem; také klávesa F1. |

Nastavení a Nápověda se otevírají uvnitř hlavního okna, stejně jako Přehled nebo
Studio. Odchod ze stránky Nastavení s neuloženými změnami se zeptá, zda uložit,
zahodit, nebo zůstat. Modely a Záloha zůstávají samostatnými okny.

Karta **Dynamika** na Přehledu přidává pod souhrnné karty dva grafy: sloupcový
graf aktivity za 14 dní (dnešek zcela vpravo, nad každým nenulovým dnem
hodnota) a graf rozdělení jazyků a enginů podle nejčastějších hodnot, zbytek
sloučený do "jiné". Oba grafy se kreslí přímo na stránce a překreslují při
změně velikosti okna; když v období není žádná aktivita, místo prázdných
sloupců se zobrazí text o chybějící aktivitě. Stavový řádek na každé stránce
zobrazuje tenký indikátor průběhu, dokud běží úloha — ve výchozím stavu
neurčitý, při stahování modelu se přepne na procenta — a dokud běží fronta
přepisů, vedle něj kompaktní počítadlo "hotovo/celkem".

## Profily

| Profil | Rozpoznávání | AI oprava | Síť |
|---|---|---|---|
| **Lokální Ollama** | Ollama audio model | Ollama automaticky | Pouze loopback |
| **Lokální Whisper** | Faster Whisper | Vypnuto | Místní |
| **OpenAI cloud** | Uložený OpenAI STT model | OpenAI ručně | Výslovný souhlas |

Nastavení modulu je přímo pod kartami profilů a přepíná se spolu s vybraným
profilem: model Ollama a lokální AI oprava, parametry Faster Whisper, nebo
modely a klíč OpenAI. Samostatná záložka Lokální AI již neexistuje. Pokud žádný
nainstalovaný model Ollama neuvádí capability `audio`, seznam nabídne všechny
nainstalované modely s upozorněním a volba zůstává na vás.

## Obecné

| Pole | Chování |
|---|---|
| **Jazyk rozhraní** | `Українська`, `Čeština`, `English`; mění i články Nápovědy. |
| **Uchování zvuku** | `keep` nebo `delete_after_transcription`; originál zůstává. |
| **Globální zkratka** | Výchozí `<f13>`; lze zachytit novou kombinaci. |
| **Automaticky kopírovat** | Ve výchozím stavu vypnuto. |
| **Pouze offline** | Určuje profil; místní profily cloud blokují. |

## Synchronizace

Lokální zrcadlo přepisů — soukromá alternativa cloudové synchronizace: aplikace
sama nic neodesílá do sítě, jen zapisuje soubory do zvolené složky, kterou lze
nasměrovat na jakoukoli složku, kterou si sami synchronizuje váš vlastní klient
třetí strany (Google Drive, OneDrive apod.).

| Pole | Chování |
|---|---|
| **Zrcadlit přepisy do složky** | Zapíná automatické zrcadlení; vyžaduje zadanou složku. |
| **Složka synchronizace** + **Vybrat…** | Volí se přes systémový dialog výběru složky. |
| **Kopírovat i zvuk** | Přidá do zrcadla uchovanou spravovanou kopii zvuku, pokud existuje. |
| **Synchronizovat vše nyní** | Zrcadlí všechny uložené přepisy na pozadí a zobrazí souhrn ve stavovém řádku. |

Každý přepis se zapisuje jako dvojice `Markdown` + `JSON` (deterministické názvy
souborů podle data a id); aplikace ze složky nic nemaže a neukládá do ní žádné
přístupové klíče. Neplatná složka (neexistuje, je to soubor, symlink, nebo leží
uvnitř/kolem privátní datové složky) se při ukládání odmítne s vysvětlením chyby.
Zrcadlení se také spouští automaticky po dokončení rozpoznávání, uložení úprav v
editoru, přiřazení mluvčího a po použití AI čištění; případná chyba zrcadlení se
jen zobrazí ve stavovém řádku a nikdy nezruší samotný zápis. Cesta se ukládá
jako rozvinutá absolutní (zápis `~/Drive` se převede na plnou cestu) a kontrola
složky se opakuje před každým zápisem: složka, která zmizela, byla nahrazena
symlinkem nebo přesunuta dovnitř datové složky, vyvolá jen zprávu ve stavovém
řádku a aplikace ji nikdy nevytváří znovu. Smazání záznamu z Historie nesmaže
jeho zrcadlené soubory.

## Rozpoznávání

| Pole | Účel |
|---|---|
| **Modul** | Aktuální engine určený profilem. |
| **Jazyk přepisu** | `auto`, `uk`, `cs`, `en`; nezávisí na jazyku UI. |
| **Model / Zařízení / Typ výpočtu** | Uložené hodnoty Faster Whisper. |

V profilu Lokální Whisper je **Zařízení** omezeno na `auto`, `cpu` nebo
`cuda` a **Typ výpočtu** používá podporovaný slovník CTranslate2. **Zjistit
hardware** provede omezenou místní kontrolu na pozadí; nenačte model ani
nezmění uložené nastavení. Pokud kontrola není dostupná, ponechte bezpečné
`auto/default`. Zvolené kombinace se před načtením modelu ověří proti místnímu
runtime.

**VAD filtr pauz** je ve výchozím stavu zapnutý a platí pouze pro profil
Lokální Whisper. Vypněte jej, pokud filtr ořezává tichou řeč; ekvivalenty v CLI
jsou `--vad` a `--no-vad`. Profily Ollama a OpenAI toto nastavení ignorují.
| **Model OpenAI STT** | Používá jen cloudový profil. |
| **Slovník JSON** | Deterministické nahrazení po rozpoznání. |
| **Lokální model Ollama** | Instalovaný model s capability `audio`; pokud žádný není, všechny instalované modely s upozorněním. |
| **Obnovit** | Na pozadí znovu zkontroluje modely Ollama. |
| **OpenAI key** | OS keychain; není v settings.json. |

## Nástroje editoru ve Studiu

| Tlačítko | Akce |
|---|---|
| **B**, **I** | Tučné a kurzíva pro vybraný text. |
| **Hledat a nahradit** | Otevře panel pod editorem: hledání s ohledem na velikost písmen a celá slova, počet shod, „Nahradit“ a „Nahradit vše“. |
| **Přidat do slovníku** | Z vybraného textu vytvoří pravidlo a uloží spravovaný slovník. |
| **Výplňková slova** | Ukáže každou nalezenou shodu v kontextu; odstraní se jen zaškrtnuté. |
| **Skóre jistoty** | Vypíše segmenty s nejnižším skóre jistoty k prohlédnutí. |

Tyto nástroje mění pouze text v editoru. Do úložiště se změny dostanou
až po „Uložit úpravy“; `raw_text` zůstává beze změny. „Přidat do slovníku“ je
nedostupné, když je otevřený externí slovník jen ke čtení nebo když má stránka
„Slovník“ neuložené změny. Výplňková slova se berou ze seznamu pro jazyk
záznamu, a je-li `auto`, z jazyka rozpoznávání v nastavení.

### Prohlídka podle skóre jistoty

„Skóre jistoty“ otevře panel se seznamem segmentů, jejichž skóre je pod prahem;
nejnižší jsou první. Skóre je vlastní signál jistoty rozpoznávacího modelu pro
daný segment: není to pravděpodobnost chyby ani záruka přesnosti, seznam tedy
udává pořadí prohlídky, ne verdikt. Segmenty, u kterých model skóre neuvedl,
jsou vypsané za oskórovanými a označené jako „bez skóre“.

Práh začíná na 0.60 a lze jej nastavit mezi 0.00 a 1.00. Patří jen otevřené
stránce: nikdy se nezapisuje do nastavení ani na disk, takže další relace opět
začíná na 0.60. Výběr řádku zvýrazní odpovídající segment v editoru a přesune
tam kurzor; pokud je text segmentu už přepsaný k nepoznání, panel to oznámí
místo skoku. „Přehrát segment“ spustí místní přehrávání uloženého zvuku od
začátku daného segmentu.

### Místní přehrávání

Panel přehrávání pod editorem přehrává uloženou spravovanou kopii zvuku:
přehrát/pauza, stop, posun o ±5 sekund a rychlost 0.75–2×. Rychlost je řešená
převzorkováním, vyšší tempo tedy zvyšuje i výšku hlasu. Posuvník pozice
sleduje přehrávání a tažením jej lze přesunout na libovolné místo v nahrávce;
dokud jej držíte, pozice se nemění a k posunu dojde až při puštění, a bez
přehratelného zvuku je posuvník neaktivní. Přehrává se pouze spravovaná kopie
v úložišti aplikace; externí originální soubor se nikdy nevyhledává ani
neotevírá. Pokud zvuk záznamu není uložen, panel to oznámí. Přepnutí stránky
nebo záznamu i obnovení ze zálohy přehrávání zastaví.

## Fronta přepisů

| Ovládací prvek | Účel |
|---|---|
| **Fronta** | Zobrazí nebo skryje panel **Fronta přepisů** nad editorem. |
| **Přidat soubory…** | Otevře výběr souborů; přidají se jen podporované mediální formáty. |
| **Přidat složku…**, **Včetně podsložek** | Přidá všechny podporované soubory ze složky, rekurzivně, je-li zaškrtnuto. |
| **Spustit** | Spustí zpracování čekajících souborů jeden po druhém s aktuálním profilem. |
| **Pozastavit** / **Pokračovat** | Zastaví frontu po aktuálním souboru, nebo v ní pokračuje. |
| **Přeskočit** | Přeskočí vybrané soubory, které ještě čekají. |
| **Odebrat dokončené** | Vymaže řádky se stavem hotovo, chyba, přeskočeno nebo zrušeno. |
| **Vyprázdnit** | Vyprázdní frontu; odmítnuto, dokud se soubor zpracovává. |

Sloupce tabulky jsou **Soubor**, **Stav**, **Sekundy** a **Chyba**. Fronta
pojme nejvýše 500 souborů. Položka se stavem chyba nebo zrušeno zaznamená
důvod ve sloupci **Chyba** a fronta pokračuje dalším souborem. V editoru se
otevře jen poslední úspěšný přepis, nikdy přes neuložené úpravy; zavření
aplikace frontu pozastaví.

## Karta Chytrý text

| Ovládací prvek | Účel |
|---|---|
| **Pauza, s** | Mezera mezi segmenty, po které začíná nový odstavec (0–600 s, výchozí 2.0). |
| **Odstavec, s** | Nejvyšší délka odstavce, po které se rozdělí (5–3600 s, výchozí 90). |
| **Časové značky**, **Mluvčí** | Zahrnou do textu časové značky nebo značky mluvčích. |
| **Obnovit** | Přestaví náhled z aktuálního přepisu a nastavení. |
| **Kopírovat** | Zkopíruje text do schránky. |
| **Export MD…**, **Export TXT…** | Uloží text jako Markdown nebo plain text; výchozí název souboru je název zdroje bez přípony. |
| **Segmenty**, **Přiřadit mluvčího…** | Seznam každého segmentu (pořadí · čas · úryvek); vyberte jej a zadejte jméno pro označení, nebo pole ponechte prázdné pro odebrání značky. |

Neplatná hodnota **Pauza, s** nebo **Odstavec, s** zobrazí ve stavovém řádku
hlášení „Pauza musí být 0 až 600 s a odstavec 5 až 3600 s.“ a náhled se
vyprázdní. Značky mluvčích se ukládají pouze do metadat přepisu; `raw_text`
a text segmentů se nikdy nemění. Pokud není vybraný žádný přepis, karta
zobrazí „Vyberte záznam, aby se zobrazil chytrý text.“

## Formáty a soukromí

Podporované vstupy: WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV,
WEBM; nejvýše 2 GiB a dvě hodiny. Ollama má samostatný limit 30 minut na jeden
přepis. Export: TXT, MD, JSON, SRT a VTT. Ollama vytváří jeden časový úsek.

## Obnova katalogu modelů

GUI při spuštění jednou sjednotí místní katalog Faster Whisper a příkazy pro
správu modelů jej sjednotí před provedením operace. Platí to pro `models list`,
`models install`, `models verify`, `models remove` i pro výslovný příkaz
obnovy. Obnova je místní a offline; sama nestahuje, neaktualizuje ani nemaže
model.

Kontrolu nebo obnovu lze vyvolat přímo:

```text
voice-studio models reconcile
```

Příkaz vypíše jeden objekt JSON s poli `status` (`PASS` nebo `FAIL`), `action`
(`none`, `repaired` nebo `attention`), `adopted`, `dropped`, `blocked` (každá
blokovaná položka má `id`, `path` a `reason`), `staging_removed`,
`staging_kept`, `residue_removed` a `catalog_quarantined` (cesta nebo `null`).
Při selhání přidá také `error`. Běžné příkazy `models` oznámí netriviální výsledek na
stderr s prefixem `model-catalog:`; zdravý nezměněný katalog zůstane tichý.

## Audit úložiště a výslovná oprava

Spusťte kontrolu úložiště pouze pro čtení:

```text
voice-studio storage audit
```

Samotný příkaz auditu nikdy nesjednocuje katalog modelů a nemění aktivní datový
strom. SQLite čte ze stabilního dočasného snímku databáze a WAL mimo datový
strom, poté tento snímek odstraní, a spravované zdroje, modely a exporty skenuje
bez zápisu. Hlavní `status` nadále popisuje stav SQLite a spravovaných zdrojů.
`missing_records` označuje záznamy přepisů s chybějícím spravovaným zdrojem.
Vnořený objekt `model_catalog` hlásí manifest, chybějící, osiřelé a blokované
modely, staging a zbytky. Vnořený objekt `exports` uvádí běžné `files`,
konzervativní kandidáty `canonical_stale`, soubory `unmanaged` a nebezpečné nebo
nesouborové položky `blocked`. Kandidáti exportu jsou pouze hlášení a nikdy se
automaticky nemažou.

Automatické sjednocení nadále probíhá při spuštění GUI a před každým příkazem
`models`. `voice-studio models reconcile` je přímý výslovný příkaz. Pokud
záznam přepisu odkazuje na spravovaný zdroj, jehož chybění je prokázáno,
odpojte pouze tento zastaralý odkaz:

```text
voice-studio storage repair-missing TRANSCRIPT_ID --expected-path PATH --yes
```

`--expected-path` chrání před opravou záznamu, který se od auditu změnil;
použijte přesnou cestu z `missing_records`. Oprava vymaže pouze uložený odkaz na
zdroj a příznak uchování. Nikdy nemaže ani znovu nevytváří zvuk, nemění text
přepisu a nedotkne se původního mediálního souboru uživatele.

## Záloha a místní stav

Obnova nahradí ze zálohy přepisy, nastavení a uložené zdroje. Aktuální místní
stromy tohoto počítače `models/` a `exports/` zůstanou beze změny; záměrně jsou
**mimo archiv** a do `.voice-backup` se nikdy nepřidávají.

Před změnou živého kořene program zkontroluje tyto stromy bez následování
odkazů. Symlink, junction nebo jiný Windows reparse point (stejně jako jiný
nebezpečný speciální prvek) obnovu přeruší **před** změnou živého kořene.
Zobrazí se konkrétní chyba, například `local restore state contains an unsafe
path: <path>`; současná data zůstanou na místě.

Ollama používá jen `127.0.0.1:11434`; cloud není fallback; `raw_text` je
neměnný; API keys nejsou v nastavení, metadata, záloze ani diagnostice.
