# Přehled obrazovek a nastavení

## Levé menu

| Položka | Účel |
|---|---|
| **Studio** | Nahrávání, přepis, editor a historie. |
| **Modely** | Správa pouze modelů Faster Whisper. |
| **Záloha** | Vytvoření, ověření a obnovení zálohy. |
| **Nastavení** | Profily, jazyky, Ollama, Whisper a OpenAI. |
| **Nápověda** | Tento manuál; také klávesa F1. |

## Profily

| Profil | Rozpoznávání | AI oprava | Síť |
|---|---|---|---|
| **Lokální Ollama** | Ollama audio model | Ollama automaticky | Pouze loopback |
| **Lokální Whisper** | Faster Whisper | Vypnuto | Místní |
| **OpenAI cloud** | Uložený OpenAI STT model | OpenAI ručně | Výslovný souhlas |

## Obecné

| Pole | Chování |
|---|---|
| **Jazyk rozhraní** | `Українська`, `Čeština`, `English`; mění i články Nápovědy. |
| **Uchování zvuku** | `keep` nebo `delete_after_transcription`; originál zůstává. |
| **Globální zkratka** | Výchozí `<f13>`; lze zachytit novou kombinaci. |
| **Automaticky kopírovat** | Ve výchozím stavu vypnuto. |
| **Pouze offline** | Určuje profil; místní profily cloud blokují. |

## Rozpoznávání a Lokální AI

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
| **Lokální model Ollama** | Instalovaný model s capability `audio`. |
| **Obnovit** | Na pozadí znovu zkontroluje modely Ollama. |
| **OpenAI key** | OS keychain; není v settings.json. |

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
