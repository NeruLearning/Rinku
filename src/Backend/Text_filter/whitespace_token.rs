use std::collections::HashMap;

/// Die emotionalen Kategorien für das Twitch-Sentiment
#[derive(Debug, PartialEq, Eq, Hash, Clone, Copy)]
pub enum SentimentCategory {
    Laughter,   // z.B. LULW, KEKW, haha
    Hype,       // z.B. Pog, PogChamp, Clap
    Negative,   // z.B. WeirdChamp, L, ResidentSleeper
}

/// Struktur für das aggregierte Ergebnis eines 2-Sekunden-Batches
#[derive(Debug, Default)]
pub struct SentimentResult {
    pub laughter: i32,
    pub hype: i32,
    pub negative: i32,
    pub total_clean_messages: i32,
}

/// Analysiert einen Batch von Twitch-Nachrichten mittels hocheffizienter Whitespace-Tokenisierung
pub fn analyze_batch_sentiment(
    messages: &[String], 
    emote_dict: &HashMap<&str, SentimentCategory>
) -> SentimentResult {
    
    let mut result = SentimentResult::default();
    result.total_clean_messages = messages.len() as i32;

    for msg in messages {
        // 1. Schritt: Den Satz stur bei jedem Leerzeichen zerhacken (Zero-Allocation)
        for raw_word in msg.split_whitespace() {
            
            // 2. Schritt: Säuberung von Satzzeichen am Anfang und Ende des Wortes.
            // Aus "KEKW!!!" oder "(LULW)" wird reines "KEKW" oder "LULW".
            // is_ascii_punctuation deckt ab: !, ., ,, ?, (, ), [, ], {, }, etc.
            let clean_word = raw_word.trim_matches(|c: char| c.is_ascii_punctuation());

            // 3. Schritt: Blitzschneller O(1) Lookup in der HashMap
            if let Some(category) = emote_dict.get(clean_word) {
                match category {
                    SentimentCategory::Laughter => result.laughter += 1,
                    SentimentCategory::Hype => result.hype += 1,
                    SentimentCategory::Negative => result.negative += 1,
                }
            }
        }
    }

    result
}

fn main() {
    // --- SETUP: Das machen wir einmalig beim Start des Bots ---
    let mut emote_dict = HashMap::new();
    
    // Wir mappen die Emotes auf unsere Kategorien
    emote_dict.insert("LULW", SentimentCategory::Laughter);
    emote_dict.insert("KEKW", SentimentCategory::Laughter);
    emote_dict.insert("haha", SentimentCategory::Laughter);
    
    emote_dict.insert("Pog", SentimentCategory::Hype);
    emote_dict.insert("PogChamp", SentimentCategory::Hype);
    
    emote_dict.insert("WeirdChamp", SentimentCategory::Negative);
    emote_dict.insert("ResidentSleeper", SentimentCategory::Negative);

    // --- LIVE-BETRIEB: Das passiert alle 2 Sekunden im Rust-Filter ---
    // Ein beispielhafter Batch von Nachrichten, die reingekommen sind
    let chat_batch = vec![
        "Das war so ultra witzig KEKW!".to_string(),
        "Neuro ist einfach die Beste PogChamp Pog".to_string(),
        "Was redet sie da schon wieder... WeirdChamp, echt jetzt.".to_string(),
        "haha LULW lulw (das lulw wird ignoriert wegen Groß/Kleinschreibung)".to_string(),
    ];

    // Analyse starten
    let ergebnis = analyze_batch_sentiment(&chat_batch, &emote_dict);

    // --- ERGEBNIS-CHECK ---
    println!("--- Batch Analyse Ergebnisse ---");
    println!("Lacher gezählt: {}", ergebnis.laughter);   // Erwartet: 3 (KEKW, haha, LULW)
    println!("Hype gezählt:   {}", ergebnis.hype);       // Erwartet: 2 (PogChamp, Pog)
    println!("Negativ gezählt: {}", ergebnis.negative);   // Erwartet: 1 (WeirdChamp)
    println!("Nachrichten im Batch: {}", ergebnis.total_clean_messages);
}