use std::sync::atomic::{AtomicU8, AtomicU64, Ordering};

pub(crate) const STATE_NEW: u8 = 0;
pub(crate) const STATE_STARTING: u8 = 1;
pub(crate) const STATE_STARTED: u8 = 2;
pub(crate) const STATE_STOPPING: u8 = 3;
pub(crate) const STATE_STOPPED: u8 = 4;
pub(crate) const STATE_FAILED: u8 = 5;

pub(crate) const FATAL_NONE: u8 = 0;
pub(crate) const FATAL_NULL_BUFFER: u8 = 1;
pub(crate) const FATAL_OVERSIZED_CALLBACK: u8 = 2;

pub(crate) struct EngineStats {
    pub(crate) callback_count: AtomicU64,

    pub(crate) input_ring_dropped_blocks: AtomicU64,
    pub(crate) input_ring_dropped_frames: AtomicU64,

    pub(crate) output_ring_dropped_blocks: AtomicU64,
    pub(crate) output_ring_dropped_frames: AtomicU64,

    pub(crate) output_underrun_blocks: AtomicU64,
    pub(crate) output_underrun_frames: AtomicU64,

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

            output_ring_dropped_blocks: AtomicU64::new(0),
            output_ring_dropped_frames: AtomicU64::new(0),

            output_underrun_blocks: AtomicU64::new(0),
            output_underrun_frames: AtomicU64::new(0),

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

    pub(crate) fn fatal_error_name(&self) -> &'static str {
        match self.fatal_error.load(Ordering::Relaxed) {
            FATAL_NONE => "none",
            FATAL_NULL_BUFFER => "null_buffer",
            FATAL_OVERSIZED_CALLBACK => "oversized_callback",
            _ => "unknown",
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
