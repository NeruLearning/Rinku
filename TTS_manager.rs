use futures_util::{StreamExt, sinkExt};
use serde_json;
use tokio::sync::mpsc;
use tokio_tungstenite::{connect_async, tungstenite::protocol::Message};

async fn tts_streaming_task(mut words_rx: mpsc::Receiver<String>) {
    let tts_url = TTS_port;
    let (ws_stream, _) = connect_async(tts_url)
        .await
        .expect("Failed to connect to TTS server");
    let (mut ws_write, mut ws_read) = ws_stream.split();

    tokio::spawn(async move {
        while let Some(word) = words_rx.recv().await {
            let msg = Message::Text(word);
            if let Err(e) = ws_write.send(msg).await {
                eprintln!("Error sending message: {}", e);
            }
        }
    });
    while let Some(msg) = ws_read.next().await {
        if let Ok(Message::Binary(audio_bytes)) = msg {
            play_audio(audio_bytes);
        }
    }
}
