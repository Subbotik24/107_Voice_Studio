# Hlavní postupy

## Přepis pomocí lokální Ollama

1. Spusťte Ollama.
2. V **Nastavení → Profily** zvolte **Lokální Ollama**.
3. V **Lokální AI** vyberte audio model a nastavení uložte.
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
  a externí originály se do archivu nevkládají.

Pokud obnovení přeruší výpadek napájení nebo nucené ukončení procesu, VOICE
Studio je při příštím spuštění dokončí nebo vrátí zpět a výsledek zobrazí ve
stavovém řádku. Adresář recovery zůstane v obou případech na disku.
