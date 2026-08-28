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
| **Model OpenAI STT** | Používá jen cloudový profil. |
| **Slovník JSON** | Deterministické nahrazení po rozpoznání. |
| **Lokální model Ollama** | Instalovaný model s capability `audio`. |
| **Obnovit** | Na pozadí znovu zkontroluje modely Ollama. |
| **OpenAI key** | OS keychain; není v settings.json. |

## Formáty a soukromí

Podporované vstupy: WAV, MP3, M4A, FLAC, OGG, OPUS, AAC, MP4, MOV, MKV,
WEBM; nejvýše 2 GiB a dvě hodiny. Ollama má samostatný limit 30 minut na jeden
přepis. Export: TXT, MD, JSON, SRT a VTT. Ollama vytváří jeden časový úsek.

Ollama používá jen `127.0.0.1:11434`; cloud není fallback; `raw_text` je
neměnný; API keys nejsou v nastavení, metadata, záloze ani diagnostice.
