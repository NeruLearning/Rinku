// use std::fs::OpenOptions;
// use std::io::{Seek, SeekFrom, Write};
// use std::path::PathBuf;
// use std::sync::{Arc, Mutex};
// use std::time::Duration;

// use tantivy::{IndexWriter, doc, schema::Field};
// use tokio::sync::mpsc;

// use crate::Block;

// pub async fn thread_2(
//     mut receiver: mpsc::Receiver<Block>,
//     writer: Arc<Mutex<IndexWriter>>,
//     bin_field: Field,
//     titel_field: Field,
//     offset_field: Field,
// ) {
//     // ── Hilfsfunktionen ──────────────────────────────────────────

//     fn find_marker(haystack: &str, needle: &str) -> Option<usize> {
//         let haystack_bytes = haystack.as_bytes();
//         let needle_bytes = needle.as_bytes();
//         if needle_bytes.is_empty() {
//             return Some(0);
//         }
//         if haystack_bytes.len() < needle_bytes.len() {
//             return None;
//         }
//         for i in 0..=haystack_bytes.len() - needle_bytes.len() {
//             let mut matched = true;
//             for j in 0..needle_bytes.len() {
//                 if haystack_bytes[i + j].to_ascii_lowercase()
//                     != needle_bytes[j].to_ascii_lowercase()
//                 {
//                     matched = false;
//                     break;
//                 }
//             }
//             if matched {
//                 return Some(i);
//             }
//         }
//         None
//     }

//     fn parse_raw_block(raw: &str) -> Option<(String, String, String)> {
//         let bin_pos = find_marker(raw, "bin:")?;
//         let titel_pos = find_marker(raw, "titel:")?;
//         let text_pos = find_marker(raw, "text:")?;
//         if !(bin_pos < titel_pos && titel_pos < text_pos) {
//             return None;
//         }

//         let bin = raw[bin_pos + 4..titel_pos].trim();
//         let titel = raw[titel_pos + 6..text_pos].trim();
//         let text = raw[text_pos + 5..].trim();

//         if bin.is_empty() || titel.is_empty() || text.is_empty() {
//             return None;
//         }
//         Some((bin.to_string(), titel.to_string(), text.to_string()))
//     }

//     fn sanitize_name(name: &str) -> String {
//         let mut cleaned = String::new();
//         for ch in name.trim().chars() {
//             if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
//                 cleaned.push(ch);
//             } else if ch == ' ' {
//                 cleaned.push('_');
//             }
//         }
//         cleaned
//     }

//     fn write_binary(file: &mut std::fs::File, text: &str) -> std::io::Result<u64> {
//         let offset = file.seek(SeekFrom::End(0))?;
//         let text_bytes = text.as_bytes();
//         let length = text_bytes.len() as u32;
//         file.write_all(&length.to_le_bytes())?;
//         file.write_all(text_bytes)?;
//         file.flush()?;
//         file.sync_all()?;
//         Ok(offset)
//     }

//     // ── Setup ────────────────────────────────────────────────────

//     let bins_root = PathBuf::from("long_time_memory");
//     if let Err(err) = std::fs::create_dir_all(&bins_root) {
//         eprintln!("[thread_2] Fehler beim Erstellen des Ordners: {}", err);
//         return;
//     }

//     // data.bin für unkategorisierte Blöcke
//     // let mut data_file = OpenOptions::new()
//     //     .create(true)
//     //     .append(true)
//     //     .open("./data.bin")
//     //     .unwrap();

//     // ── Hauptloop ────────────────────────────────────────────────

//     while let Some(block) = receiver.recv().await {
//         eprintln!("[thread_2] Block empfangen: '{:?}'", block.text);

//         if let Some((bin_name, titel, text)) = parse_raw_block(&block.text) {
//             // ── Hat bin: + titel: + text: → in passende .bin Datei ──
//             let safe_name = sanitize_name(&bin_name);
//             if safe_name.is_empty() {
//                 eprintln!("[thread_2] Ungültiger bin-Name: '{}'", bin_name);
//                 continue;
//             }

//             let bin_path = bins_root.join(format!("{}.bin", safe_name));
//             let mut bin_file = match OpenOptions::new().create(true).append(true).open(&bin_path) {
//                 Ok(f) => f,
//                 Err(err) => {
//                     eprintln!("[thread_2] Fehler beim Öffnen von {:?}: {}", bin_path, err);
//                     continue;
//                 }
//             };

//             // Text binär schreiben → Offset merken
//             let offset = match write_binary(&mut bin_file, &text) {
//                 Ok(o) => o,
//                 Err(err) => {
//                     eprintln!(
//                         "[thread_2] Fehler beim Schreiben in {:?}: {}",
//                         bin_path, err
//                     );
//                     continue;
//                 }
//             };

//             eprintln!(
//                 "[thread_2] Geschrieben in {:?} | bin={} titel={} offset={}",
//                 bin_path, bin_name, titel, offset
//             );

//             // Tantivy: bin → titel → offset
//             {
//                 let mut w = writer.lock().unwrap();
//                 w.add_document(doc!(
//                     bin_field    => bin_name,
//                     titel_field  => titel,
//                     offset_field => offset,
//                 ))
//                 .unwrap();
//                 w.commit().unwrap();
//             }
//         } else {
//             // ── Kein bin: prefix → in data.bin ──
//             let mut data_file = OpenOptions::new()
//                 .create(true)
//                 .append(true)
//                 .open("./data.bin")
//                 .unwrap();

//             let offset = write_binary(&mut data_file, &block.text).unwrap(); // ← offset speichern

//             // Tantivy: data.bin unkategorisiert
//             {
//                 let mut w = writer.lock().unwrap();
//                 w.add_document(doc!(
//                     bin_field    => "data",
//                     titel_field  => block.speaker.clone(),
//                     offset_field => offset,
//                 ))
//                 .unwrap();
//                 w.commit().unwrap();
//             }
//         }

//         tokio::spawn(async move {
//             tokio::time::sleep(Duration::from_secs(60)).await;
//             eprintln!("[thread_2] Block nach 1 Minute bestätigt");
//         });
//     }
// }
