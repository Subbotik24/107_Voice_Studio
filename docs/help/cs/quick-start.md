# Rychlý start

Cíl: vytvořit první soukromý přepis pomocí místní Ollama.

## 1. Spuštění

Otevřete **VOICE Studio.exe**. Program zobrazí rovnou hlavní okno; průvodce
počátečním nastavením se automaticky neotevírá. U portable verze nejprve
rozbalte celý ZIP včetně sousední složky **VOICE Studio**.

## 2. Kontrola místního modelu

1. Spusťte Ollama ve Windows.
2. Otevřete **Nastavení → Profily**.
3. Ponechte **Lokální Ollama**.
4. Na kartě **Lokální AI** zkontrolujte **Lokální model Ollama**.
5. Je-li seznam prázdný, zvolte **Obnovit** a potom **Uložit**.

Při prvním spuštění VOICE Studio na pozadí vyhledá pouze modely, které Ollama
označí jako modely se vstupem `audio`. Není-li nic uloženo, vhodný model vybere
a uloží. Již uložený model nikdy tiše nenahradí.

## 3. Vstupní soubor

Použijte WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV nebo WEBM s
hlasem. Soubor musí obsahovat zvuk, nesmí být prázdný a nesmí přesáhnout 2 GiB
ani dvě hodiny. Profil **Lokální Ollama** přijímá nejvýše 30 minut na jeden
přepis; delší nahrávku zpracujte profilem **Lokální Whisper**.

## 4. Přepis

1. Na obrazovce **Studio** zvolte **Přepsat soubor…**.
2. Vyberte soubor.
3. Vyčkejte na import, přepis, uložení a místní AI opravu.
4. Operaci lze zastavit tlačítkem **Zrušit**.

Zvuk jde pouze do Ollama na `127.0.0.1:11434`. Whisper ani OpenAI se
nespustí jako skrytá náhrada.

## 5. Výsledek

- **Opravený text** je pracovní verze;
- **Originál** je neměnný první výstup rozpoznávání;
- **Data** obsahují technické údaje;
- záznam se uloží do **Historie**.

Selže-li druhý požadavek Ollama pro opravu textu, samotný přepis zůstane uložený
a zobrazí se s upozorněním.

## 6. Další kroky

[Nahrávání z mikrofonu](workflows.md#nahrávání-z-mikrofonu)

[Přepnutí profilu](workflows.md#přepnutí-profilu)

[Úpravy a export](workflows.md#úpravy-a-export)

[Ollama není dostupná](troubleshooting.md#ollama-není-dostupná)
