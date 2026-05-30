use tokio::sync::mpsc;

pub async fn thread_2(
    mut receiver: mpsc::Receiver<Block>, // Receiver for blocks of data or each stiring ??
    writer: Arc<Mutex<IndexWriter>>,
    body_field: Field,
    speaker_field: Field, // combine speaker and body to one field ???
    offset_field: Field,
    timestamp_field: Field,
) {
    fn find_marker(haystack: &str, needle: &str) -> Option<usize> {
        let haystack_bytes = haystack.as_bytes();
        let needle_bytes = needle.as_bytes();
        if needle_bytes.is_empty() {
            return Some(0);
        }
        if haystack_bytes.len() < needle_bytes.len() {
            return None;
        }
        for i in 0..=haystack_bytes.len() - needle_bytes.len() {
            let mut matched = true;
            for j in 0..needle_bytes.len() {
                if haystack_bytes[i + j].to_ascii_lowercase()
                    != needle_bytes[j].to_ascii_lowercase()
                {
                    matched = false;
                    break;
                }
            }
            if matched {
                return Some(i);
            }
        }
        None
    }

    fn parse_raw_block(raw: &str) -> Option<(String, String, String)> {
        let bin_pos = find_marker(raw, "bin:")?;
        let titel_pos = find_marker(raw, "titel:")?;
        let text_pos = find_marker(raw, "text:")?;
        if !(bin_pos < titel_pos && titel_pos < text_pos) {
            return None;
        }

        let bin = raw[bin_pos + 4..titel_pos].trim();
        let titel = raw[titel_pos + 6..text_pos].trim();
        let text = raw[text_pos + 5..].trim();

        if bin.is_empty() || titel.is_empty() || text.is_empty() {
            return None;
        }

        Some((bin.to_string(), titel.to_string(), text.to_string()))
    }

    fn sanitize_bin_name(name: &str) -> String {
        let mut cleaned = String::new();
        for ch in name.trim().chars() {
            if ch.is_ascii_alphanumeric() || ch == '-' || ch == '_' {
                cleaned.push(ch);
            } else if ch == ' ' {
                cleaned.push('_');
            }
        }
        cleaned
    }

    fn split_label_value<'a>(line: &'a str, label: &str) -> Option<&'a str> {
        let trimmed = line.trim_start();
        if trimmed.len() < label.len() {
            return None;
        }
        let (head, tail) = trimmed.split_at(label.len());
        if head.eq_ignore_ascii_case(label) {
            Some(tail)
        } else {
            None
        }
    }

    struct Section {
        title: String,
        text: String,
    }

    fn parse_sections(content: &str) -> Vec<Section> {
        let mut sections = Vec::new();
        let mut current_title: Option<String> = None;
        let mut current_text = String::new();

        for line in content.lines() {
            if let Some(rest) = split_label_value(line, "Titel:") {
                if let Some(title) = current_title.take() {
                    sections.push(Section {
                        title,
                        text: current_text.trim_end().to_string(),
                    });
                }
                current_title = Some(rest.trim().to_string());
                current_text.clear();
                continue;
            }

            if let Some(rest) = split_label_value(line, "Text:") {
                let rest = rest.trim();
                if !rest.is_empty() {
                    current_text.push_str(rest);
                    current_text.push('\n');
                }
                continue;
            }

            if current_title.is_some() {
                current_text.push_str(line);
                current_text.push('\n');
            }
        }

        if let Some(title) = current_title.take() {
            sections.push(Section {
                title,
                text: current_text.trim_end().to_string(),
            });
        }

        sections
    }

    fn build_sections(sections: &[Section]) -> String {
        let mut out = String::new();
        for (idx, section) in sections.iter().enumerate() {
            if idx > 0 {
                out.push('\n');
            }
            out.push_str("Titel: ");
            out.push_str(section.title.trim());
            out.push('\n');
            out.push_str("Text:\n");
            out.push_str(section.text.trim_end());
            out.push('\n');
        }
        out
    }

    fn upsert_section_content(content: &str, title: &str, text: &str) -> String {
        let title = title.trim();
        let text = text.trim();
        if title.is_empty() || text.is_empty() {
            return content.to_string();
        }

        let mut sections = parse_sections(content);
        let title_key = title.to_lowercase();
        if let Some(section) = sections
            .iter_mut()
            .find(|section| section.title.trim().to_lowercase() == title_key)
        {
            if !section.text.is_empty() && !section.text.ends_with('\n') {
                section.text.push('\n');
            }
            section.text.push_str(text);
        } else {
            sections.push(Section {
                title: title.to_string(),
                text: text.to_string(),
            });
        }

        build_sections(&sections)
    }

    fn upsert_bin_section(
        base_dir: &std::path::Path,
        bin_name: &str,
        title: &str,
        text: &str,
    ) -> std::io::Result<()> {
        let safe_name = sanitize_bin_name(bin_name);
        if safe_name.is_empty() {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "bin name is empty",
            ));
        }

        let bin_path = base_dir.join(format!("{}.bin", safe_name));
        let content = std::fs::read_to_string(&bin_path).unwrap_or_default();
        let updated = upsert_section_content(&content, title, text);
        std::fs::write(&bin_path, updated)
    }

    let bins_root = std::path::PathBuf::from("Long_time_memory");
    if let Err(err) = std::fs::create_dir_all(&bins_root) {
        eprintln!("thread_2: failed to create bins dir: {}", err);
    }

    let dummy_path = std::path::PathBuf::from("dummy_rohtext.txt");
    if dummy_path.exists() {
        match std::fs::read_to_string(&dummy_path) {
            Ok(raw) => {
                if let Some((bin_name, title, text)) = parse_raw_block(&raw) {
                    if let Err(err) = upsert_bin_section(&bins_root, &bin_name, &title, &text) {
                        eprintln!("thread_2: failed to write dummy bin: {}", err);
                    }
                } else {
                    eprintln!("thread_2: dummy rohtext has invalid format");
                }
            }
            Err(err) => {
                eprintln!("thread_2: failed to read dummy rohtext: {}", err);
            }
        }
    }

    let mut data_file = OpenOptions::new()
        .create(true)
        .append(true)
        .open("path to data.bin") // -> path file felxible machen
        .unwrap();

    while let Some(block) = receiver.recv().await {
        if let Some((bin_name, title, text)) = parse_raw_block(&block.text) {
            if let Err(err) = upsert_bin_section(&bins_root, &bin_name, &title, &text) {
                eprintln!("thread_2: failed to write bin: {}", err);
            }
        }
        let offset = data_file.seek(SeekFrom::End(0)).unwrap();

        let text_bytes = block.text.as_bytes();
        let length = text_bytes.len() as u32;

        data_file.write_all(&length.to_le_bytes()).unwrap();
        data_file.write_all(text_bytes).unwrap();
        data_file.flush().unwrap();

        {
            let mut w = writer.lock().unwrap();
            w.add_document(doc!(
            offset_field => offset,
            body_field => block.text.clone(),
            speaker_field => block.speaker.clone(),
            timestamp_field => block.timestamp,
            ))
            .unwrap();
            w.commit().unwrap();
        }

        let _text_log = block.text.clone();
        tokio::spawn(async move {
            tokio::time::sleep(Duration::from_secs(60)).await;
        });
    }
}
