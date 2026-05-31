# RS File Uebersicht

Diese Datei beschreibt ausfuehrlich, was jede Rust-Datei im Projekt macht und wie die Daten durch das System fliessen.

## Gesamtueberblick

Das Programm besteht aus drei asynchronen Aufgaben ("Threads"), die ueber einen Kanal verbunden sind:

1) stdin wird zu Bloecken zusammengefasst und als `Block` an den Kanal gesendet.
2) Empfangenene Bloecke werden in binaere Dateien geschrieben und in Tantivy indiziert.
3) Eine Suchschleife liest Suchanfragen von stdin, durchsucht den Index und gibt gefundene Abschnitte aus.

Wichtige Datenstruktur:

- `Block` (in `main.rs` definiert):
  - `text`: kompletter Block-Text
  - `speaker`: "user" oder "gpt"
  - `timestamp`: Unix-Timestamp (Sekunden)

Binaeres Speicherformat:

- Jede Nachricht wird als Laenge + Bytes gespeichert.
- Laenge: `u32` in Little-Endian.
- Danach folgen die UTF-8 Bytes des Textes.

Dateiorte:

- Index: `./long_time_memory/index` (Tantivy)
- Binaerdateien pro Bin: `./long_time_memory/<bin>.bin`
- Fallback-Datei fuer unklassifizierte Bloecke: `./data.bin`

## src/main.rs

Zweck:

- Einstiegspunkt der App.
- Initialisiert Tantivy-Index und Schema.
- Startet die drei asynchronen Aufgaben mit `tokio::join!`.

Wichtige Schritte:

- Baut das Schema mit Feldern:
  - `bin` (TEXT | STORED)
  - `titel` (TEXT | STORED)
  - `offset` (FAST | STORED)
- Legt den Index unter `./long_time_memory/index` an oder oeffnet ihn.
- Erstellt einen `IndexWriter` (50 MB).
- Initialisiert einen Tokio-Kanal (Kapazitaet 8).
- Startet:
  - `long_time_memory::text_pipe::thread_1` (Eingabe -> Kanal)
  - `long_time_memory::rohdatei_to_bin::thread_2` (Kanal -> Bin-Datei + Index)
  - `long_time_memory::search_memory::thread_3` (Suche ueber stdin)

## src/long_time_memory/mod.rs

Zweck:

- Modul-Export fuer die drei Untermodule.
- Macht `rohdatei_to_bin`, `search_memory` und `text_pipe` von aussen erreichbar.

## src/long_time_memory/text_pipe.rs

Zweck:

- Liest `stdin` zeilenweise ein.
- Gruppiert mehrere Zeilen zu einem Block, getrennt durch Leerzeilen.
- Erzeugt einen `Block` und sendet ihn ueber den Kanal.

Ablauf im Detail:

- Liest Zeilen asynchron mit `tokio`.
- Eine Leerzeile signalisiert: Block ist fertig.
- Der Blocktext wird getrimmt und als `Block` versendet.
- Sprecher-Erkennung:
  - `user:` Prefix -> `speaker = "user"`
  - `gpt:` Prefix -> `speaker = "gpt"`
  - sonst Standard: `speaker = "user"`
- Am Ende wird der letzte Block ebenfalls gesendet, falls keine abschliessende Leerzeile vorhanden ist.

## src/long_time_memory/rohdatei_to_bin.rs

Zweck:

- Nimmt Bloecke vom Kanal entgegen.
- Parst Spezialformat `bin: ... titel: ... text: ...`.
- Schreibt den Text binaer in eine Bin-Datei.
- Indiziert die Metadaten in Tantivy.

Ablauf im Detail:

- Der Block wird auf drei Marker untersucht (case-insensitiv):
  - `bin:`
  - `titel:`
  - `text:`
- Nur wenn die Reihenfolge `bin -> titel -> text` stimmt und alle Felder nicht leer sind, wird der Block als strukturierter Eintrag behandelt.

Wenn die Marker vorhanden sind:

- Der Bin-Name wird bereinigt:
  - Erlaubt: alphanumerisch, `-`, `_`, und Leerzeichen (Leerzeichen werden zu `_`).
- Es wird in `./long_time_memory/<bin>.bin` geschrieben:
  - Laenge (`u32`, Little-Endian), danach Text-Bytes.
- `offset` (Dateiposition) wird gespeichert.
- Tantivy-Dokument wird hinzugefuegt:
  - `bin = <bin>`
  - `titel = <titel>`
  - `offset = <offset>`

Wenn die Marker fehlen:

- Der Block wird in `./data.bin` geschrieben.
- Tantivy-Dokument:
  - `bin = "data"`
  - `titel = <speaker>`
  - `offset = <offset>`

Nebenwirkung:

- Nach jedem Block wird ein Task gestartet, der nach 60 Sekunden eine Bestaetigung ausgibt.

## src/long_time_memory/search_memory.rs

Zweck:

- Liest Suchanfragen von `stdin`.
- Fuehrt eine Tantivy-Suche aus.
- Gibt gefundene Texte aus `data.bin` als Zusammenfassung aus.

Ablauf im Detail:

- Erwartet Zeilen, die mit `search_memory` beginnen.
- Der Rest der Zeile ist die Suchanfrage.
- Tantivy `QueryParser` durchsucht die Felder `body_field` und `speaker_field`.
- Es werden die Top 10 Treffer geholt.
- Fuer jeden Treffer:
  - `offset` wird gelesen.
  - Aus `data.bin` wird der gespeicherte Text gelesen.
- Die Treffer werden mit `---` getrennt zusammengefuegt.
- Ausgabe via `eprint!` mit Prefix `search_memory_result:`.

Wichtiger Hinweis zur aktuellen Implementierung:

- Die Suche liest immer aus `data.bin`, auch wenn der Indexeintrag zu einem anderen `bin` gehoert.
- Das bedeutet: Treffer aus anderen Bin-Dateien koennen aktuell nicht korrekt geladen werden.
