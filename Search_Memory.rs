use std::fs::OpenOptions;
use std::io::{self, BufRead, Write, Read, Seek, SeekFrom};
use std::path::Path;
use std::sync::{Arc, Mutex};
use std::time::Duration;
use tokio::sync::mpsc;
use tantivy::{schema::*, Index, IndexWriter, doc, TantivyDocument};
use tantivy::query::QueryParser;
use tantivy::collector::TopDocs;
use serde::{Deserialize, Serialize};


use tantivy::{
    schema::*,
    Index,
    IndexWriter,
    doc,
};









// Thread 3 Search Memory

async fn  thread_3(
    index: Arc<Index>,
    body_field: Field,
    speaker_field: Field,
    offset_field: Field,
    schema: Schema,
) {

    let stdin: io::stdin();
    for line in stdin.lock().lines() {
        let line = match line {
            Ok(line) => line,
            Err(_) => break,
        };
        if !line.starts_with("search_memory") {
            continue;
        }

        let anfrage = &line[7..];
        eprintln!(anfrage);




    // tantivy search
    let reader = index.reader().unwrap();
    let searcher = reader.searcher();

    let query_parser = QueryParser::for_index(&index, vec![body_field, speaker_field]);
    let query =  match query_parser.parse_query(anfrage) {
        Ok(q) => q,
        Err(_) => break,
    };

    let top_docs = searcher.search(&query, &TopDocs::with_limit()).unwrap(); // -> refix?
    let mut abschnitte: Vec<String> = Vec::new();
    for (_score, doc_address) in top_docs {
        let doc: TantivyDocument = searcher.doc(doc_address).unwrap();

        if let Some(offset_val) = doc.get_first(offset_field) {
            if let Some(offset) = offset_val.as_u64() {
                let mut data_file = std::fs::File::open("data.bin").unwrap(); // file path opener 
                data_file.seek(SeekFrom::Start(offset)).unwrap();

                let mut laenge_bytes = [0u8; 4];
                if data_file.read_exact(&mut laenge_bytes).is_err() {
                    continue;
                }
                let laenge = u32::from_le_bytes(laenge_bytes) as usize;

                let mut text_bytes = vec![0u8; laenge];
                if data_file.read_exact(&mut text_bytes).is_err() {
                    continue;
                }
                if let Ok(text) = String::from_utf8(text_bytes) {
                    abschnitte.push(text);
                }
            }
        }
    }

    let summary = if abschnitte.is_empty() {
        String::new()
    } else {
        // AI 
    };
    // send summary to thread GPT
}
}