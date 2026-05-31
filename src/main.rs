use std::sync::{Arc, Mutex};
use tantivy::{Index, schema::*};
use tokio::sync::mpsc;

#[derive(Debug)]
pub struct Block {
    pub text: String,
    pub speaker: String,
    pub timestamp: i64,
}
mod long_time_memory;

#[tokio::main]
async fn main() -> tantivy::Result<()> {
    let mut schema_builder = Schema::builder();
    let bin_field = schema_builder.add_text_field("bin", TEXT | STORED);
    let titel_field = schema_builder.add_text_field("titel", TEXT | STORED);
    let offset_field = schema_builder.add_u64_field("offset", FAST | STORED);
    let schema = schema_builder.build();

    let index_path = std::path::Path::new("./long_time_memory/index");
    std::fs::create_dir_all(index_path).unwrap();
    let index = if index_path.join("meta.json").exists() {
        Index::open_in_dir(index_path)?
    } else {
        Index::create_in_dir(index_path, schema.clone())?
    };

    let writer = Arc::new(Mutex::new(index.writer(50_000_000)?));
    let index = Arc::new(index);
    let index_clone = Arc::clone(&index);

    let (sender, receiver) = mpsc::channel(8);

    tokio::join!(
        long_time_memory::text_pipe::thread_1(sender),
        long_time_memory::rohdatei_to_bin::thread_2(
            receiver,
            writer,
            bin_field,
            titel_field,
            offset_field,
        ),
        long_time_memory::search_memory::thread_3(
            index_clone,
            bin_field,
            titel_field,
            offset_field,
            schema
        ),
    );

    Ok(())
}
