// use crate::Block;
// use chrono::Utc;
// use tokio::io::AsyncWriteExt;
// use tokio::io::{AsyncBufReadExt, BufReader};
// use tokio::sync::{mpsc, watch};
// use tokio::time::{Duration, interval};

// pub async fn thread_1(sender: mpsc::Sender<Block>, run_finished_rx: watch::Receiver<bool>) {
//     let (stream_reader, stream_writer) = tokio::io::duplex(4096);
//     let reader = BufReader::new(stream_reader);
//     let mut lines = reader.lines();
//     let mut buffer = String::new();
//     let mut stream_writer = stream_writer;

//     tokio::spawn(async move {
//         let mut ticker = interval(Duration::from_secs(20));

//         loop {
//             ticker.tick().await;

//             let transcript = match tokio::fs::read_to_string("transkript2.txt").await {
//                 Ok(content) => content,
//                 Err(err) if err.kind() == std::io::ErrorKind::NotFound => continue,
//                 Err(err) => {
//                     eprintln!("[thread_1] Konnte Transkript nicht lesen: {}", err);
//                     continue;
//                 }
//             };

//             let lines: Vec<String> = transcript
//                 .lines()
//                 .map(|line| line.trim_end_matches('\r').trim().to_string())
//                 .filter(|line| !line.is_empty())
//                 .collect();

//             if lines.is_empty() {
//                 if *run_finished_rx.borrow() {
//                     eprintln!("[thread_1] run beendet und transkript leer, feeder stoppt");
//                     break;
//                 }
//                 continue;
//             }

//             let take_count = lines.len().min(4);
//             let chunk = lines[..take_count].join(" ");
//             let remaining = if take_count >= lines.len() {
//                 String::new()
//             } else {
//                 format!("{}\n", lines[take_count..].join("\n"))
//             };

//             let schema_block = format!(
//                 "bin: personality\ntitel: personality\nthema: live_transkript\ntext: {}\n\n",
//                 chunk
//             );

//             if let Err(err) = stream_writer.write_all(schema_block.as_bytes()).await {
//                 eprintln!("[thread_1] Konnte Chunk nicht in Stream schreiben: {}", err);
//                 break;
//             }
//             if let Err(err) = stream_writer.flush().await {
//                 eprintln!("[thread_1] Konnte Stream nicht flushen: {}", err);
//                 break;
//             }

//             if let Err(err) = tokio::fs::write("transkript2.txt", remaining).await {
//                 eprintln!(
//                     "[thread_1] Konnte verarbeitete Zeilen nicht loeschen: {}",
//                     err
//                 );
//             }
//         }
//     });

//     while let Ok(Some(line)) = lines.next_line().await {
//         let line = line.trim_end_matches('\r').to_string(); // Windows \r\n fix

//         if line.is_empty() {
//             // Leerzeile = Block fertig → schicken
//             if !buffer.is_empty() {
//                 let text = buffer.trim().to_string();
//                 eprintln!("[thread_1] Block gesendet: '{:?}'", text);

//                 let (speaker, text) = if text.starts_with("user:") {
//                     ("user".to_string(), text[5..].to_string())
//                 } else if text.starts_with("gpt:") {
//                     ("gpt".to_string(), text[4..].to_string())
//                 } else {
//                     ("user".to_string(), text)
//                 };

//                 sender
//                     .send(Block {
//                         text,
//                         speaker,
//                         timestamp: Utc::now().timestamp(),
//                     })
//                     .await
//                     .unwrap();

//                 buffer.clear();
//             }
//         } else {
//             // Zeile zum Buffer hinzufügen
//             buffer.push_str(&line);
//             buffer.push('\n');
//         }
//     }

//     // Letzten Block schicken falls keine Leerzeile am Ende
//     if !buffer.is_empty() {
//         let text = buffer.trim().to_string();
//         eprintln!("[thread_1] Letzter Block gesendet: '{:?}'", text);

//         sender
//             .send(Block {
//                 text,
//                 speaker: "user".to_string(),
//                 timestamp: Utc::now().timestamp(),
//             })
//             .await
//             .unwrap();
//     }
// }

use crate::llm_config::ollama_min::{ClassifiedText, classify_text};
use serde::Deserialize;
use tokio::sync::mpsc;
use tokio::sync::watch;
use zmq;

#[derive(Deserialize, Debug)]
struct TranscriptPayload {
    text: String,
    // source: String,
    emotion: Option<String>,
    speed: Option<String>,
    volume: Option<String>,
}
// thread 1
pub async fn text_pipe(
    sender: mpsc::Sender<ClassifiedText>,
    run_finished_rx: watch::Receiver<bool>,
) {
    let context = zmq::Context::new();

    let mic_socket = context.socket(zmq::PULL).unwrap();
    mic_socket.connect("tcp://127.0.0.1:5555").unwrap();
    mic_socket.set_rcvtimeo(0).unwrap();

    let system_socket = context.socket(zmq::PULL).unwrap();
    system_socket.connect("tcp://127.0.0.1:5556").unwrap();
    system_socket.set_rcvtimeo(0).unwrap();

    let twitch_socket = context.socket(zmq::PULL).unwrap();
    twitch_socket.connect("tcp://127.0.0.1:5557").unwrap();
    twitch_socket.set_rcvtimeo(0).unwrap();

    loop {
        if *run_finished_rx.borrow() {
            break;
        }

        for (socket, source) in [
            (&mic_socket, "mic"),
            (&system_socket, "system"),
            (&twitch_socket, "twitch"),
        ] {
            match socket.recv_string(0) {
                Ok(Ok(msg)) => {
                    let payload: TranscriptPayload = match serde_json::from_str(&msg) {
                        Ok(p) => p,
                        Err(e) => {
                            eprintln!("[thread_1] JSON Parse Fehler: {}", e);
                            continue;
                        }
                    };

                    let sender = sender.clone();
                    let source = source.to_string();

                    tokio::spawn(async move {
                        classify_text(
                            &payload.text,
                            &source,
                            payload.emotion.as_deref(),
                            payload.volume.as_deref(),
                            payload.speed.as_deref(),
                            sender,
                        )
                        .await;
                    });
                }
                Ok(Err(_)) => continue,
                Err(zmq::Error::EAGAIN) => continue,
                Err(e) => eprintln!("[thread_1] ZMQ Fehler {}: {}", source, e),
            }
        }
    }
}
