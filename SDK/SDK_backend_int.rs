use async_trait::async_trait;
use futures_util::{SinkExt, StreamExt};
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;
use tokio::net::TcpListener;
use tokio_tungstenite::accept_async;
use tokio_tungstenite::tungstenite::Message;

// Das Interface, das JEDE Spieldatei erfüllen muss
#[async_trait]
pub trait GameAgent: Send + Sync {
    fn name(&self) -> &str;
    async fn process_state(&self, raw_json: &str) -> Result<String, String>;
}

pub struct CoreHub {
    registry: HashMap<String, Box<dyn GameAgent>>,
}

impl CoreHub {
    pub fn new() -> Self {
        Self {
            registry: HashMap::new(),
        }
    }

    // Hier werden die Dateien/Module angemeldet
    pub fn register(&mut self, agent: Box<dyn GameAgent>) {
        self.registry.insert(agent.name().to_string(), agent);
    }

    pub async fn run(self, addr: &str) {
        let listener = TcpListener::bind(addr).await.unwrap();
        let shared_registry = Arc::new(self.registry);
        println!("Core Server läuft auf {}", addr);

        while let Ok((stream, _)) = listener.accept().await {
            let registry_clone = Arc::clone(&shared_registry);
            tokio::spawn(async move {
                let mut ws = accept_async(stream).await.unwrap();

                while let Some(Ok(Message::Text(text))) = ws.next().await {
                    // 1. Schau nur kurz ins JSON, um den Namen des Spiels zu finden
                    let parsed: Value = serde_json::from_str(&text).unwrap_or_default();
                    let game_name = parsed["game"].as_str().unwrap_or("unknown");

                    // 2. DYNAMISCHER AUFRUF in der jeweiligen Zieldatei!
                    if let Some(agent) = registry_clone.get(game_name) {
                        match agent.process_state(&text).await {
                            Ok(response) => {
                                let _ = ws.send(Message::Text(response)).await;
                            }
                            Err(e) => eprintln!("Fehler in Spieldatei {}: {}", game_name, e),
                        }
                    } else {
                        eprintln!("Unbekanntes Spiel: {}", game_name);
                    }
                }
            });
        }
    }
}
