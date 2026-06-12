// use tokio::sync::{mpsc, watch};
// use tokio::time::{Duration, sleep};

// #[derive(Debug)]
// pub struct Block {
//     pub text: String,
//     pub speaker: String,
//     pub timestamp: i64,
// }

// mod long_time_memory;
// mod whisper_files;

// #[tokio::main]
// async fn main() {
//     let (sender, mut receiver) = mpsc::channel::<Block>(32);
//     let (run_finished_tx, run_finished_rx) = watch::channel(false);

//     let run_finished_tx_for_whisper = run_finished_tx.clone();

//     let whisper_task = tokio::task::spawn_blocking(move || {
//         whisper_files::whisper::run();
//         let _ = run_finished_tx_for_whisper.send(true);
//     });

//     // thread_1 startet 4 Sekunden nach Start von whisper::run
//     sleep(Duration::from_secs(4)).await;
//     tokio::spawn(long_time_memory::text_pipe::thread_1(
//         sender,
//         run_finished_rx,
//     ));

//     while let Some(block) = receiver.recv().await {
//         println!(
//             "[main] block angekommen | speaker={} | ts={} | text={}",
//             block.speaker, block.timestamp, block.text
//         );
//     }

//     let _ = whisper_task.await;
// }
// main.rs

// main.rs

mod llm_config;
mod long_time_memory;

use llm_config::ollama_min::ClassifiedText;
use tokio::sync::mpsc;
use tokio::sync::watch;

#[tokio::main]
async fn main() {
    let (sender, mut receiver) = mpsc::channel::<ClassifiedText>(100);
    let (run_finished_tx, run_finished_rx) = watch::channel(false);

    tokio::spawn(long_time_memory::text_pipe::text_pipe(
        sender,
        run_finished_rx,
    ));

    while let Some(classified) = receiver.recv().await {
        println!("[main] Empfangen: {:?}", classified);
    }

    run_finished_tx.send(true).unwrap();
}
