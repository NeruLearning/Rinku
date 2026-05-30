use std::sync::{Arc, Mutex};
use tantivy::{Index, schema::*};
use tokio::sync::mpsc;

#[derive(Debug)]
pub struct Block {
    pub text: String,
    pub speaker: String,
    pub timestamp: i64,
}

#[path = "../Long_time_memory/rohdatei_to_bin.rs"]
mod rohdatei_to_bin;

#[tokio::main]
async fn main() -> tantivy::Result<()> {
    let input_path = std::env::args().nth(1).unwrap_or_else(|| {
        if std::path::Path::new("Roh_2.txt").exists() {
            "Roh_2.txt".to_string()
        } else {
            "dummy_rohtet_2.txt".to_string()
        }
    });

    let raw = match std::fs::read_to_string(&input_path) {
        Ok(text) => text,
        Err(err) => {
            eprintln!("failed to read {}: {}", input_path, err);
            return Ok(());
        }
    };

    let mut schema_builder = Schema::builder();
    let body_field = schema_builder.add_text_field("body", TEXT | STORED);
    let speaker_field = schema_builder.add_text_field("speaker", TEXT | STORED);
    let offset_field = schema_builder.add_u64_field("offset", FAST | STORED);
    let timestamp_field = schema_builder.add_i64_field("timestamp", FAST | STORED);
    let schema = schema_builder.build();

    let index = Index::create_in_ram(schema);
    let writer = Arc::new(Mutex::new(index.writer(50_000_000)?));

    let (sender, receiver) = mpsc::channel(8);

    let worker = tokio::spawn(long_time_memory::rohdatei_to_bin::thread_2(
        receiver,
        writer,
        body_field,
        speaker_field,
        offset_field,
        timestamp_field,
    ));

    let _ = sender
        .send(Block {
            text: raw,
            speaker: "tester".to_string(),
            timestamp: 0,
        })
        .await;
    drop(sender);

    let _ = worker.await;
    Ok(())
}
