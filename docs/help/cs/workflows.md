# Hlavní postupy

## Přepis pomocí lokální Ollama

1. Spusťte Ollama.
2. V **Nastavení → Profily** zvolte **Lokální Ollama**.
3. Pod kartami profilů vyberte audio model a nastavení uložte.
4. Ve **Studio** zvolte **Přepsat soubor…**.

Ollama vrací text bez spolehlivých časových značek. VOICE Studio proto vytvoří
jeden úsek od nuly po skutečnou délku záznamu a časování si nevymýšlí.

## Přepnutí profilu

V **Nastavení → Profily** vyberte kartu a stiskněte **Uložit**.

- **Lokální Ollama** — výchozí: Ollama místně přepisuje i automaticky opravuje.
- **Lokální Whisper** — Faster Whisper vytváří časované úseky; automatická AI
  oprava je vypnutá.
- **OpenAI cloud** — cloudový přepis; před každým odesláním se zobrazí název,
  velikost souboru a žádost o souhlas.

Profil nastaví související privacy a engine hodnoty. Modely a podrobnosti se
uchovají po restartu. Při chybě se jiný profil nespustí automaticky.

## Nahrávání z mikrofonu

1. Pro krátký záznam držte **● Podržte pro nahrávání**, mluvte a tlačítko
   uvolněte.
2. Pro souvislý záznam stiskněte **● Trvalé nahrávání** a ukončete ho tlačítkem
   **■ Zastavit nahrávání**.

Jeden záznam může trvat nejvýše dvě hodiny. Při hlášení o poškozeném záznamu je
bezpečné záznam odmítnout a zopakovat.

## Úpravy a export

1. Otevřete přepis z výsledku nebo **Historie**.
2. Upravujte kartu **Opravený text** a zvolte **Uložit úpravy**.
3. Exportujte jako **TXT**, **MD**, **JSON**, **SRT** nebo **VTT**.

**Originál** je pouze ke čtení. SRT/VTT z profilu Ollama mají jeden úsek přes
celou délku; pro přesné segmenty použijte **Lokální Whisper**.
Po uložení ruční úpravy používají TXT/MD a SRT/VTT stejný opravený text. Úprava
přes hranice časovaných segmentů je sloučí do jejich existujícího vnějšího
intervalu; aplikace žádný časový kód nevytváří, nedělí ani neposouvá.

## Fronta přepisů

1. Ve **Studio** zvolte **Fronta** a otevřete panel **Fronta přepisů** nad
   editorem.
2. Přidejte soubory tlačítkem **Přidat soubory…**, nebo **Přidat složku…**
   (zaškrtněte **Včetně podsložek** pro rekurzivní přidání). Do fronty se
   přidají jen podporované mediální formáty; stavový řádek oznámí, kolik
   souborů bylo přidáno a kolik odmítnuto.
3. Zvolte **Spustit**. Soubory se zpracují jeden po druhém s aktuálním
   profilem — stejný engine, jazyk a nastavení jako u jednoho souboru;
   cloudový profil i tak žádá souhlas u každého souboru. **Pozastavit** /
   **Pokračovat** zastaví frontu po dokončení aktuálního souboru, nebo v ní
   pokračuje. **Přeskočit** vynechá jen vybrané soubory, které ještě čekají.
   **Odebrat dokončené** vymaže dokončené řádky. **Vyprázdnit** je odmítnuto,
   dokud se soubor zpracovává.

Každý výsledek se uloží do **Historie**. Neúspěšný soubor zaznamená důvod ve
sloupci **Chyba** a fronta pokračuje dalším souborem bez chybového okna.
Zrušení běžící úlohy označí soubor jako „Zrušeno“ a frontu pozastaví. Po
dokončení fronty stavový řádek zobrazí souhrn a v editoru se otevře jen
poslední úspěšný přepis — nikdy přes neuložené úpravy. Zavření aplikace
frontu pozastaví.

## Chytrý text

1. Otevřete přepis a zvolte kartu **Chytrý text** vedle **Data**.
2. Nastavte **Pauza, s** (mezera, po které začíná nový odstavec) a
   **Odstavec, s** (nejvyšší délka odstavce) a zaškrtněte **Časové značky**
   nebo **Mluvčí**, chcete-li je zahrnout. Zvolte **Obnovit**, nebo stiskněte
   Enter v některém z polí, pro přestavění náhledu.
3. Zvolte **Kopírovat** pro zkopírování textu, nebo **Export MD…** /
   **Export TXT…** pro uložení do souboru.
4. Pro označení mluvčího vyberte segment v seznamu **Segmenty**, zvolte
   **Přiřadit mluvčího…** a zadejte jméno — prázdné pole značku odebere.

Neplatná hodnota **Pauza, s** nebo **Odstavec, s** se ohlásí ve stavovém
řádku a náhled se vyprázdní. Značky mluvčích se ukládají pouze do metadat
přepisu; `raw_text` a každý segment zůstávají přesně tak, jak byly
rozpoznány.

## Ruční AI oprava

Po uložení ručních změn zvolte **AI úprava…**, zkontrolujte Before/After a
potvrďte. Funkce **Vrátit AI úpravu** obnoví předchozí opravený text. Neměnný
`raw_text` se neodesílá ani nemění.

## Historie a záloha

- Hledejte podle části názvu zdroje a použijte **Hledat** nebo Enter.
- **Přejmenovat** nemění název zvukového souboru.
- **Odstranit** se samostatně ptá na spravovanou kopii zvuku; původní soubor
  uživatele nikdy nemaže.
- **Záloha** vytváří, ověřuje a obnovuje `.voice-backup`. Ollama/Whisper modely
  a externí originály se do archivu nevkládají. Spravované kopie zvuku jsou ve
  výchozím stavu zahrnuty; zrušte volbu zahrnutí zvuku (nebo použijte CLI
  `--without-audio`), pokud je nechcete přidat. Ve výchozím stavu vzniká
  plaintext záloha v1. Zaškrtávací pole **Zašifrovat heslem** zapíná
  encrypted backup v2: po výběru souboru se aplikace dvakrát zeptá na heslo
  v maskovaných polích. **Ztracené heslo nelze obnovit** — bez něj je archiv
  nečitelný. Heslo se nikdy neukládá do nastavení, journalu obnovení ani
  diagnostics. Ověření nebo obnovení zašifrovaného archivu se zeptá jednou;
  špatné heslo je chyba autentizace bez čtení plaintext a zrušení dotazu
  nemění data ani nemaže recovery state.

Pokud obnovení přeruší výpadek napájení nebo nucené ukončení procesu, VOICE
Studio je při příštím spuštění dokončí nebo vrátí zpět a výsledek zobrazí ve
stavovém řádku. Adresář recovery zůstane v obou případech na disku.
