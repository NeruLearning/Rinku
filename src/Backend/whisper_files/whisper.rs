use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
use std::fs::OpenOptions;
use std::io::Write;
use std::sync::{Arc, Mutex};
use whisper_rs::{FullParams, SamplingStrategy, WhisperContext, WhisperContextParameters};

fn transcribe_chunk(ctx: &WhisperContext, audio: &[f32]) -> Option<String> {
    let mut params = FullParams::new(SamplingStrategy::Greedy { best_of: 1 });
    params.set_language(Some("en"));
    params.set_print_special(false);
    params.set_print_progress(false);
    params.set_print_realtime(false);
    params.set_print_timestamps(false);
    params.set_suppress_blank(true);

    let mut state = ctx.create_state().expect("failed to create state");
    state.full(params, audio).ok()?;

    let mut result = String::new();
    for segment in state.as_iter() {
        let text = segment.to_string();
        let trimmed = text.trim();
        if trimmed.is_empty() {
            continue;
        }
        // Nur ASCII + deutsche Sonderzeichen
        let is_valid = trimmed
            .chars()
            .all(|c| c.is_ascii() || matches!(c, 'ä' | 'ö' | 'ü' | 'Ä' | 'Ö' | 'Ü' | 'ß'));
        if !is_valid {
            continue;
        }
        // Halluzinationen filtern
        let lower = trimmed.to_lowercase();
        if lower.starts_with('[')
            || lower.starts_with('(')
            || lower.contains("music")
            || lower.contains("sighs")
            || lower.contains("subtitles")
            || lower.contains("blank")
            || lower.contains("audio")
            || lower.contains("transcript")
            || lower.contains("see you in")
            || lower.contains("next video")
        {
            continue;
        }
        result.push_str(trimmed);
        result.push(' ');
    }

    let result = result.trim().to_string();
    if result.is_empty() {
        None
    } else {
        Some(result)
    }
}

fn save_to_file(text: &str) {
    let mut file = OpenOptions::new()
        .create(true)
        .append(true)
        .open("transkript2.txt")
        .expect("Konnte transkript2.txt nicht öffnen");
    writeln!(file, "{}", text).expect("Schreiben fehlgeschlagen");
}

fn resample_to_16khz(samples: &[f32], original_rate: u32) -> Vec<f32> {
    if original_rate == 16000 {
        return samples.to_vec();
    }
    let ratio = 16000.0 / original_rate as f64;
    let new_len = (samples.len() as f64 * ratio) as usize;
    let mut resampled = Vec::with_capacity(new_len);
    for i in 0..new_len {
        let src_idx = i as f64 / ratio;
        let src_idx_floor = src_idx as usize;
        let frac = src_idx - src_idx_floor as f64;
        let a = samples.get(src_idx_floor).copied().unwrap_or(0.0);
        let b = samples.get(src_idx_floor + 1).copied().unwrap_or(0.0);
        resampled.push(a + (b - a) * frac as f32);
    }
    resampled
}

fn start_stream() -> (cpal::Stream, Arc<Mutex<Vec<f32>>>, u32) {
    let host = cpal::default_host();
    let device = host.default_input_device().expect("Kein Mikrofon gefunden");
    let config = device.default_input_config().expect("Keine Konfiguration");
    let sample_rate = config.sample_rate().0;
    let channels = config.channels() as usize;

    let audio_buffer: Arc<Mutex<Vec<f32>>> = Arc::new(Mutex::new(Vec::new()));
    let audio_buffer_clone = audio_buffer.clone();

    let stream = device
        .build_input_stream(
            &config.into(),
            move |data: &[f32], _| {
                let mut buf = audio_buffer_clone.lock().unwrap();
                // Stereo zu Mono
                let mut i = 0;
                while i + channels <= data.len() {
                    let mut sample = 0.0f32;
                    for c in 0..channels {
                        sample += data[i + c];
                    }
                    buf.push(sample / channels as f32);
                    i += channels;
                }
            },
            |err| eprintln!("Stream-Fehler: {}", err),
            None,
        )
        .expect("Stream konnte nicht erstellt werden");

    (stream, audio_buffer, sample_rate)
}

fn live_loop(
    ctx: &WhisperContext,
    audio_buffer: Arc<Mutex<Vec<f32>>>,
    sample_rate: u32,
    stop_signal: Arc<Mutex<bool>>,
) {
    // Mindestens 5 Sekunden Audio für sinnvolle Transkription
    let min_samples = (sample_rate * 5) as usize;

    loop {
        std::thread::sleep(std::time::Duration::from_millis(1000));

        if *stop_signal.lock().unwrap() {
            break;
        }

        let chunk: Vec<f32> = {
            let mut buf = audio_buffer.lock().unwrap();
            if buf.len() < min_samples {
                continue;
            }
            let data = buf.clone();
            buf.clear();
            data
        };

        let resampled = resample_to_16khz(&chunk, sample_rate);

        if let Some(text) = transcribe_chunk(ctx, &resampled) {
            println!("📝 {}", text);
            save_to_file(&text);
        }
    }
}

pub fn modus_1(ctx: &WhisperContext) {
    println!("=== Modus 1: Push-to-Talk ===");
    println!("ENTER drücken zum Starten, nochmal ENTER zum Stoppen.");
    println!("Programm mit Strg+C beenden.\n");

    loop {
        print!("[ ENTER zum Aufnehmen... ]");
        std::io::stdout().flush().unwrap();
        let mut input = String::new();
        std::io::stdin().read_line(&mut input).unwrap();

        println!("🎙️  Aufnahme läuft... ENTER zum Stoppen");

        let (stream, audio_buffer, sample_rate) = start_stream();
        stream.play().expect("Stream konnte nicht gestartet werden");

        let stop_signal = Arc::new(Mutex::new(false));
        let stop_clone = stop_signal.clone();
        let audio_clone = audio_buffer.clone();

        // Live-Transkription in eigenem Thread
        let ctx_ptr = ctx as *const WhisperContext as usize;
        let handle = std::thread::spawn(move || {
            let ctx_ref = unsafe { &*(ctx_ptr as *const WhisperContext) };
            live_loop(ctx_ref, audio_clone, sample_rate, stop_clone);
        });

        // Warten auf Enter zum Stoppen
        let mut input2 = String::new();
        std::io::stdin().read_line(&mut input2).unwrap();
        *stop_signal.lock().unwrap() = true;

        handle.join().expect("Thread-Fehler");
        drop(stream);

        // Letzten Audio-Rest noch transkribieren
        let rest: Vec<f32> = audio_buffer.lock().unwrap().clone();
        if !rest.is_empty() {
            let resampled = resample_to_16khz(&rest, sample_rate);
            if let Some(text) = transcribe_chunk(ctx, &resampled) {
                println!("📝 {}", text);
                save_to_file(&text);
            }
        }

        println!("⏹️  Aufnahme gestoppt.\n");
    }
}

pub fn modus_3(ctx: &WhisperContext) {
    println!("=== Modus 3: Dauerhaft Live ===");
    println!("Programm mit Strg+C beenden.\n");

    let (stream, audio_buffer, sample_rate) = start_stream();
    stream.play().expect("Stream konnte nicht gestartet werden");
    println!("🎙️  Live-Transkription läuft...\n");

    let stop_signal = Arc::new(Mutex::new(false));
    live_loop(ctx, audio_buffer, sample_rate, stop_signal);
}

pub fn run() {
    let host = cpal::default_host();
    let device = host.default_input_device().expect("Kein Mikrofon");
    println!(
        "Mikrofon: {}",
        device.name().unwrap_or("unbekannt".to_string())
    );
    let model_path = "whisper_files\\ggml-large-v3-turbo-q8_0.bin";

    // Modus abfragen
    print!("Modus (1 = Push-to-Talk, 3 = Dauerhaft): ");
    std::io::stdout().flush().unwrap();
    let mut modus = String::new();
    std::io::stdin().read_line(&mut modus).unwrap();
    let modus = modus.trim();

    println!("Lade Modell: {}", model_path);
    let ctx = WhisperContext::new_with_params(model_path, WhisperContextParameters::default())
        .expect("Modell konnte nicht geladen werden");
    println!("✅ Modell geladen!\n");

    match modus {
        "1" => modus_1(&ctx),
        "3" => modus_3(&ctx),
        _ => eprintln!("Unbekannter Modus: {}. Bitte 1 oder 3 eingeben.", modus),
    }
}
fn main() {}
