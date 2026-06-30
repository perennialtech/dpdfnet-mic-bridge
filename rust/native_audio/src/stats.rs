use std::sync::atomic::{AtomicI64, AtomicU8, AtomicU64, Ordering};

pub(crate) const STATE_NEW: u8 = 0;
pub(crate) const STATE_STARTING: u8 = 1;
pub(crate) const STATE_STARTED: u8 = 2;
pub(crate) const STATE_STOPPING: u8 = 3;
pub(crate) const STATE_STOPPED: u8 = 4;
pub(crate) const STATE_FAILED: u8 = 5;

pub(crate) const FATAL_NONE: u8 = 0;
pub(crate) const FATAL_NULL_BUFFER: u8 = 1;
pub(crate) const FATAL_OVERSIZED_CALLBACK: u8 = 2;

const UNSET_NS: i64 = i64::MIN;

pub(crate) struct EngineStats {
    pub(crate) callback_count: AtomicU64,

    pub(crate) input_ring_dropped_blocks: AtomicU64,
    pub(crate) input_ring_dropped_frames: AtomicU64,
    pub(crate) input_stale_dropped_blocks: AtomicU64,
    pub(crate) input_stale_dropped_frames: AtomicU64,

    pub(crate) output_ring_dropped_blocks: AtomicU64,
    pub(crate) output_ring_dropped_frames: AtomicU64,
    pub(crate) output_stale_dropped_blocks: AtomicU64,
    pub(crate) output_stale_dropped_frames: AtomicU64,

    pub(crate) output_underrun_blocks: AtomicU64,
    pub(crate) output_underrun_frames: AtomicU64,

    pub(crate) current_input_queue_frames: AtomicU64,
    pub(crate) max_input_queue_frames: AtomicU64,
    pub(crate) current_output_queue_frames: AtomicU64,
    pub(crate) max_output_queue_frames: AtomicU64,

    pub(crate) callback_input_frames: AtomicU64,
    pub(crate) callback_output_frames: AtomicU64,
    first_capture_render_delta_ns: AtomicI64,
    last_capture_render_delta_ns: AtomicI64,

    pub(crate) portaudio_input_underflow: AtomicU64,
    pub(crate) portaudio_input_overflow: AtomicU64,
    pub(crate) portaudio_output_underflow: AtomicU64,
    pub(crate) portaudio_output_overflow: AtomicU64,
    pub(crate) portaudio_priming_output: AtomicU64,

    pub(crate) oversized_callback_count: AtomicU64,
    pub(crate) null_callback_count: AtomicU64,

    pub(crate) fatal_error: AtomicU8,
}

impl EngineStats {
    pub(crate) fn new() -> Self {
        Self {
            callback_count: AtomicU64::new(0),

            input_ring_dropped_blocks: AtomicU64::new(0),
            input_ring_dropped_frames: AtomicU64::new(0),
            input_stale_dropped_blocks: AtomicU64::new(0),
            input_stale_dropped_frames: AtomicU64::new(0),

            output_ring_dropped_blocks: AtomicU64::new(0),
            output_ring_dropped_frames: AtomicU64::new(0),
            output_stale_dropped_blocks: AtomicU64::new(0),
            output_stale_dropped_frames: AtomicU64::new(0),

            output_underrun_blocks: AtomicU64::new(0),
            output_underrun_frames: AtomicU64::new(0),

            current_input_queue_frames: AtomicU64::new(0),
            max_input_queue_frames: AtomicU64::new(0),
            current_output_queue_frames: AtomicU64::new(0),
            max_output_queue_frames: AtomicU64::new(0),

            callback_input_frames: AtomicU64::new(0),
            callback_output_frames: AtomicU64::new(0),
            first_capture_render_delta_ns: AtomicI64::new(UNSET_NS),
            last_capture_render_delta_ns: AtomicI64::new(UNSET_NS),

            portaudio_input_underflow: AtomicU64::new(0),
            portaudio_input_overflow: AtomicU64::new(0),
            portaudio_output_underflow: AtomicU64::new(0),
            portaudio_output_overflow: AtomicU64::new(0),
            portaudio_priming_output: AtomicU64::new(0),

            oversized_callback_count: AtomicU64::new(0),
            null_callback_count: AtomicU64::new(0),

            fatal_error: AtomicU8::new(FATAL_NONE),
        }
    }

    pub(crate) fn add_output_drop(&self, frames: usize) {
        self.output_ring_dropped_blocks
            .fetch_add(1, Ordering::Relaxed);
        self.output_ring_dropped_frames
            .fetch_add(frames as u64, Ordering::Relaxed);
    }

    pub(crate) fn add_input_stale_drop(&self, frames: usize) {
        if frames == 0 {
            return;
        }

        self.input_stale_dropped_blocks
            .fetch_add(1, Ordering::Relaxed);
        self.input_stale_dropped_frames
            .fetch_add(frames as u64, Ordering::Relaxed);
    }

    pub(crate) fn add_output_stale_drop(&self, frames: usize) {
        if frames == 0 {
            return;
        }

        self.output_stale_dropped_blocks
            .fetch_add(1, Ordering::Relaxed);
        self.output_stale_dropped_frames
            .fetch_add(frames as u64, Ordering::Relaxed);
    }

    pub(crate) fn set_input_queue_depth(&self, frames: usize) {
        self.current_input_queue_frames
            .store(frames as u64, Ordering::Relaxed);
        update_max(&self.max_input_queue_frames, frames as u64);
    }

    pub(crate) fn set_output_queue_depth(&self, frames: usize) {
        self.current_output_queue_frames
            .store(frames as u64, Ordering::Relaxed);
        update_max(&self.max_output_queue_frames, frames as u64);
    }

    pub(crate) fn record_callback_timing(
        &self,
        frames: usize,
        input_adc_time: f64,
        output_dac_time: f64,
    ) {
        self.callback_input_frames
            .fetch_add(frames as u64, Ordering::Relaxed);
        self.callback_output_frames
            .fetch_add(frames as u64, Ordering::Relaxed);

        if !input_adc_time.is_finite() || !output_dac_time.is_finite() {
            return;
        }

        let delta_ns = ((output_dac_time - input_adc_time) * 1_000_000_000.0).round() as i64;

        let _ = self.first_capture_render_delta_ns.compare_exchange(
            UNSET_NS,
            delta_ns,
            Ordering::Relaxed,
            Ordering::Relaxed,
        );
        self.last_capture_render_delta_ns
            .store(delta_ns, Ordering::Relaxed);
    }

    pub(crate) fn capture_render_drift_ppm(&self, sample_rate: f64) -> Option<f64> {
        if sample_rate <= 0.0 {
            return None;
        }

        let first = self.first_capture_render_delta_ns.load(Ordering::Relaxed);
        let last = self.last_capture_render_delta_ns.load(Ordering::Relaxed);
        if first == UNSET_NS || last == UNSET_NS {
            return None;
        }

        let frames = self.callback_output_frames.load(Ordering::Relaxed);
        if frames == 0 {
            return None;
        }

        let elapsed_audio_seconds = frames as f64 / sample_rate;
        if elapsed_audio_seconds <= 0.0 {
            return None;
        }

        let drift_seconds = (last - first) as f64 / 1_000_000_000.0;
        Some((drift_seconds / elapsed_audio_seconds) * 1_000_000.0)
    }

    pub(crate) fn fatal_error_name(&self) -> &'static str {
        match self.fatal_error.load(Ordering::Relaxed) {
            FATAL_NONE => "none",
            FATAL_NULL_BUFFER => "null_buffer",
            FATAL_OVERSIZED_CALLBACK => "oversized_callback",
            _ => "unknown",
        }
    }
}

fn update_max(target: &AtomicU64, candidate: u64) {
    let mut current = target.load(Ordering::Relaxed);
    while candidate > current {
        match target.compare_exchange_weak(
            current,
            candidate,
            Ordering::Relaxed,
            Ordering::Relaxed,
        ) {
            Ok(_) => return,
            Err(value) => current = value,
        }
    }
}

pub(crate) fn state_name(state: u8) -> &'static str {
    match state {
        STATE_NEW => "new",
        STATE_STARTING => "starting",
        STATE_STARTED => "started",
        STATE_STOPPING => "stopping",
        STATE_STOPPED => "stopped",
        STATE_FAILED => "failed",
        _ => "unknown",
    }
}
