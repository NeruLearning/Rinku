use reqwest::Client;
use serde::{Deserialize, Serialize};
use tokio::sync::mpsc;

#[derive(Serialize)]
struct OllamaRequest {
    model: String,
    prompt: String,
    stream: bool,
    system: String,
}

#[derive(Deserialize)]
struct OllamaResponse {
    response: String,
}

#[derive(Debug)]
pub struct ClassifiedText {
    pub titel: String,
    pub thema: String,
    pub text: String,
    pub source: String,
    pub emotion: Option<String>,
    pub volume: Option<String>,
    pub speed: Option<String>,
}

pub async fn classify_text(
    text: &str,
    source: &str,
    emotion: Option<&str>,
    volume: Option<&str>,
    speed: Option<&str>,
    sender: mpsc::Sender<ClassifiedText>,
) {
    let client = Client::new();

    let request = OllamaRequest {
        model: "dolphin-mistral".to_string(),
        prompt: text.to_string(),
        stream: false,
        system:
            "You are a classification assistant. Respond ONLY in this exact format, nothing else:
Title: <exactly one word>
Topic: <exactly one word>
Text: <copy the input text word for word, do not add or change anything>"
                .to_string(),
    };

    let response = match client
        .post("http://localhost:11434/api/generate")
        .json(&request)
        .send()
        .await
    {
        Ok(r) => match r.json::<OllamaResponse>().await {
            Ok(r) => r,
            Err(e) => {
                eprintln!("[llm] Parse Fehler: {}", e);
                return;
            }
        },
        Err(e) => {
            eprintln!("[llm] Request Fehler: {}", e);
            return;
        }
    };

    println!("LLM Antwort: {}", response.response);

    let classified = parse_response(&response.response, source, emotion, volume, speed);
    sender.send(classified).await.unwrap();
}

fn parse_response(
    response: &str,
    source: &str,
    emotion: Option<&str>,
    volume: Option<&str>,
    speed: Option<&str>,
) -> ClassifiedText {
    let mut titel = String::new();
    let mut thema = String::new();
    let mut text = String::new();

    for line in response.lines() {
        if let Some(val) = line.strip_prefix("Title:") {
            titel = val.trim().to_string();
        } else if let Some(val) = line.strip_prefix("Topic:") {
            thema = val.trim().to_string();
        } else if let Some(val) = line.strip_prefix("Text:") {
            text = val.trim().to_string();
        }
    }

    ClassifiedText {
        titel,
        thema,
        text,
        source: source.to_string(),
        emotion: emotion.map(|s| s.to_string()),
        volume: volume.map(|s| s.to_string()),
        speed: speed.map(|s| s.to_string()),
    }
}
