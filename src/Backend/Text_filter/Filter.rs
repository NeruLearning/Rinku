use serde::{Deserialize, Serialize};
use serde_json;
use std::collections::HashMap;
use std::fs::OpenOptions;
use std::io::Write;
use zmq;

pub struct FilterConfig {
    pub zmq_port: u16,
    pub output_file: String,
    pub shannon_threshold: f64,
    pub levenshtein_min_dist: usize,
    pub banned_words: Vec<String>,
}

#[derive(Debug, Deserialize, Serialize, Clone)]
pub struct ChatMessage {
    pub user: String,
    pub text: String,
}

pub fn banned_word_filter(sentences: &mut Vec<ChatMessage>, banned: &[&str]) {
    sentences.retain(|msg| {
        let lower_text = msg.text.to_lowercase();
        let lower_user = msg.user.to_lowercase();
        !banned
            .iter()
            .any(|word| lower_text.contains(word) || lower_user.contains(word))
    })
}

pub fn shannon_filter(messages: &mut Vec<ChatMessage>, threshold: f64) {
    messages.retain(|msg| sentence_entropy(&msg.text) >= threshold)
}

fn shannon_entropy(words: &[&str]) -> f64 {
    if words.is_empty() {
        return 0.0;
    }

    let mut frequency: HashMap<&str, usize> = HashMap::new();
    for word in words {
        *frequency.entry(word).or_insert(0) += 1;
    }

    let total = words.len() as f64;
    let mut entropy = 0.0;

    for count in frequency.values() {
        let probability = *count as f64 / total;
        entropy -= probability * probability.log2();
    }

    entropy
}

fn sentence_entropy(sentence: &str) -> f64 {
    let words: Vec<String> = sentence
        .split_whitespace()
        .map(|w| {
            w.trim_matches(|c: char| !c.is_alphanumeric())
                .to_lowercase()
        })
        .filter(|w| !w.is_empty())
        .collect();

    let word_refs: Vec<&str> = words.iter().map(|s| s.as_str()).collect();
    shannon_entropy(&word_refs)
}

fn levenshtein_distance(a: &str, b: &str) -> usize {
    let sentence_1: Vec<char> = a.chars().collect();
    let sentence_2: Vec<char> = b.chars().collect();
    let (len_1, len_2) = (sentence_1.len(), sentence_2.len());
    let mut dp = vec![vec![0usize; len_2 + 1]; len_1 + 1];
    for i in 0..=len_1 {
        dp[i][0] = i;
    }
    for j in 0..=len_2 {
        dp[0][j] = j;
    }
    for i in 1..=len_1 {
        for j in 1..=len_2 {
            dp[i][j] = if sentence_1[i - 1] == sentence_2[j - 1] {
                dp[i - 1][j - 1]
            } else {
                1 + dp[i - 1][j].min(dp[i][j - 1].min(dp[i - 1][j - 1]))
            };
        }
    }
    dp[len_1][len_2]
}

pub fn levenshtein_dedup(sentences: &mut Vec<ChatMessage>, min_distance: usize) {
    let mut unique = 0;
    for candidate in 0..sentences.len() {
        let is_duplicate = (0..unique).any(|j| {
            levenshtein_distance(&sentences[j].text, &sentences[candidate].text) < min_distance
        });

        if !is_duplicate {
            sentences.swap(unique, candidate);
            unique += 1;
        }
    }
    sentences.truncate(unique);
}

fn run_subscriber(config: FilterConfig) {
    let ctx = zmq::Context::new();
    let socket = ctx.socket(zmq::SUB).expect("Failed to create socket");

    let zmq_addr = format!("tcp://localhost:{}", config.zmq_port);

    socket
        .connect(&zmq_addr)
        .expect("Failed to connect to socket");
    socket
        .set_subscribe(b"")
        .expect("Failed to subscribe to all messages");

    let banned: Vec<&str> = config.banned_words.iter().map(|s| s.as_str()).collect();

    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open(&config.output_file)
        .expect("Konnte Datei nicht öffnen");

    loop {
        let msg_bytes = match socket.recv_bytes(0) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let mut msg_string: Vec<ChatMessage> = match serde_json::from_slice(&msg_bytes) {
            Ok(v) => v,
            Err(e) => {
                eprintln!("Failed to parse JSON: {}", e);
                continue;
            }
        };
        banned_word_filter(&mut msg_string, &banned);
        shannon_filter(&mut msg_string, config.shannon_threshold);
        levenshtein_dedup(&mut msg_string, config.levenshtein_min_dist);

        let json_line = serde_json::to_string(&msg_string).expect("Failed to serialize JSON");

        writeln!(file, "{}", json_line).expect("Failed to write to output file");
    }
}

// fn main() {
//     let json_input = r#"
//     [
//       "Die Katze sitzt auf der Matte.",
//       "Das ist sehr interessant und wichtig für die Forschung im Bereich der künstlichen Intelligenz.",
//       "Hallo hallo hallo hallo hallo hallo.",
//       "Quantencomputer könnten die Verschlüsselung moderner Kommunikationssysteme grundlegend verändern.",
//       "Ok ok ok.",
//       "Klimawandel bedroht Ökosysteme, Landwirtschaft und die Grundversorgung von Milliarden Menschen weltweit.",
//       "Das ist das das das.",
//       "Machine Learning Modelle lernen aus Datenmuster und generalisieren auf unbekannte Eingaben.",
//       "Ja nein ja nein ja nein.",
//       "Neuroplastizität beschreibt die Fähigkeit des Gehirns, synaptische Verbindungen durch Erfahrung neu zu gestalten.",
//       "Das Wetter ist heute schön.",
//       "Antibiotikaresistenz entsteht durch Selektion resistenter Bakterienstämme bei übermäßigem oder falschem Einsatz.",
//       "Ich gehe nach Hause.",
//       "Schwarze Löcher verbiegen Raumzeit so stark, dass selbst Licht ihre Gravitationsfelder nicht verlassen kann."
//     ]
//     "#;

//     let sentences: Vec<String> =
//         serde_json::from_str(json_input).expect("Ungültiges JSON – erwartet ein Array von Strings");

//     let threshold = 2.5f64;

//     let mut scored: Vec<(f64, &String)> =
//         sentences.iter().map(|s| (sentence_entropy(s), s)).collect();

//     scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());

//     println!("Alle Saetze (sortiert nach Entropie):\n");
//     println!("{:<5} {:<10} {}", "Rank", "Entropie", "Satz");
//     println!("{}", "-".repeat(80));

//     for (i, (entropy, sentence)) in scored.iter().enumerate() {
//         let marker = if *entropy >= threshold {
//             "[OK]"
//         } else {
//             "[--]"
//         };
//         println!("{:<5} {:<10.3} {} {}", i + 1, entropy, marker, sentence);
//     }

//     println!("\n{}", "=".repeat(80));
//     println!("WICHTIGE SAETZE (Entropie >= {}):\n", threshold);

//     for (i, (_, sentence)) in scored.iter().filter(|(e, _)| *e >= threshold).enumerate() {
//         println!("  {}. {}", i + 1, sentence);
//     }
// }
