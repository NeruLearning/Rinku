use std::time::{SystemTime, UNIX_EPOCH};

#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord)]
pub enum UserStatus {
    None,
    Sub,
    Vip,
    Mod,
    Streamer,
}

#[derive(Debug, Clone)]
pub struct ChatMessage {
    pub username: String,
    pub message_text: String,
    pub status: String,
    pub timestamp: u64,
}
pub struct PipeEntry {
    banned_words: Vec<String>,
    tick_buffer: Vec<ChatMessage>,
}

impl PipeEntry {
    pub fn new(banned_words: Vec<String>) -> Self {
        Self {
            banned_words,
            tick_buffer: Vec::new(),
        }
    }

    /// Wird ununterbrochen aufgerufen, wenn der WebSocket Daten liefert.
    /// Schiebt die Nachricht einfach nur ungefiltert in den aktuellen Tick-Buffer.
    pub fn ingest_raw_line(&mut self, raw_line: &str) {
        if let Some(msg) = self.parse_irc_line(raw_line) {
            self.tick_buffer.push(msg);
        }
    }

    /// ALLE 2 SEKUNDEN AUFRUFEN: Verarbeitet den gesamten Block des aktuellen Ticks
    pub fn pop_tick_data(&mut self) -> Vec<ChatMessage> {
        // Wir nehmen alle Nachrichten aus dem Buffer und hinterlassen einen leeren Vec für den nächsten Tick
        let mut current_tick_messages = std::mem::take(&mut self.tick_buffer);

        // Erst JETZT, auf dem gesamten Block, läuft der Batch-Wortfilter
        self.banned_word_filter(&mut current_tick_messages);

        current_tick_messages
    }

    /// Der Batch-Filter, der den gesamten 2-Sekunden-Block reinigt
    fn banned_word_filter(&self, sentences: &mut Vec<ChatMessage>) {
        sentences.retain(|msg| {
            let lower_text = msg.message_text.to_lowercase();
            let lower_user = msg.username.to_lowercase();

            !self.banned_words.iter().any(|word| {
                let lower_word = word.to_lowercase();
                lower_text.contains(&lower_word) || lower_user.contains(&lower_word)
            })
        });
    }

    /// Parsen des rohen Twitch-IRC-Protokolls in das Enum-Struct
    fn parse_irc_line(&self, raw_line: &str) -> Option<ChatMessage> {
        if !raw_line.contains("PRIVMSG") {
            return None;
        }

        let mut parts = raw_line.splitn(2, " PRIVMSG ");
        let tags_part = parts.next()?;
        let remaining = parts.next()?;

        let mut text_parts = remaining.splitn(2, ':');
        text_parts.next()?;
        let message_text = text_parts.next()?.trim().to_string();

        let mut username = String::from("Anonym");
        let mut badge_str = String::new();

        for tag in tags_part.replace('@', "").split(';') {
            let mut kv = tag.splitn(2, '=');
            if let (Some(key), Some(value)) = (kv.next(), kv.next()) {
                match key {
                    "display-name" => username = value.to_string(),
                    "badges" => badge_str = value.to_string(),
                    _ => {}
                }
            }
        }

        // Auflösung der Hierarchie in das Enum
        let status = if badge_str.contains("broadcaster") {
            UserStatus::Streamer
        } else if badge_str.contains("moderator") {
            UserStatus::Mod
        } else if badge_str.contains("vip") {
            UserStatus::Vip
        } else if badge_str.contains("subscriber") {
            UserStatus::Sub
        } else {
            UserStatus::None
        };

        let timestamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        Some(ChatMessage {
            username,
            message_text,
            status,
            timestamp,
        })
    }
}
