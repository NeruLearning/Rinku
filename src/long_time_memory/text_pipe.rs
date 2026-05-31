use crate::Block;
use chrono::Utc;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::sync::mpsc;

pub async fn thread_1(sender: mpsc::Sender<Block>) {
    let stdin = tokio::io::stdin();
    let reader = BufReader::new(stdin);
    let mut lines = reader.lines();
    let mut buffer = String::new();

    while let Ok(Some(line)) = lines.next_line().await {
        let line = line.trim_end_matches('\r').to_string(); // Windows \r\n fix

        if line.is_empty() {
            // Leerzeile = Block fertig → schicken
            if !buffer.is_empty() {
                let text = buffer.trim().to_string();
                eprintln!("[thread_1] Block gesendet: '{:?}'", text);

                let (speaker, text) = if text.starts_with("user:") {
                    ("user".to_string(), text[5..].to_string())
                } else if text.starts_with("gpt:") {
                    ("gpt".to_string(), text[4..].to_string())
                } else {
                    ("user".to_string(), text)
                };

                sender
                    .send(Block {
                        text,
                        speaker,
                        timestamp: Utc::now().timestamp(),
                    })
                    .await
                    .unwrap();

                buffer.clear();
            }
        } else {
            // Zeile zum Buffer hinzufügen
            buffer.push_str(&line);
            buffer.push('\n');
        }
    }

    // Letzten Block schicken falls keine Leerzeile am Ende
    if !buffer.is_empty() {
        let text = buffer.trim().to_string();
        eprintln!("[thread_1] Letzter Block gesendet: '{:?}'", text);

        sender
            .send(Block {
                text,
                speaker: "user".to_string(),
                timestamp: Utc::now().timestamp(),
            })
            .await
            .unwrap();
    }
}
