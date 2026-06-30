use std::os::raw::{c_ulong, c_void};
use std::ptr;
use std::sync::atomic::{AtomicU8, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use numpy::{PyArray1, PyReadonlyArray1, PyReadwriteArray1};
use pyo3::prelude::*;
use pyo3::types::PyDict;

use crate::device::{build_stream_parameters, StreamDirection};
use crate::errors::runtime_error;
use crate::portaudio_ffi as ffi;
use crate::ring::SpscSampleRing;
use crate::runtime::PortAudioRuntime;
use crate::stats::{
    state_name, EngineStats, FATAL_NULL_BUFFER, FATAL_OVERSIZED_CALLBACK, STATE_FAILED, STATE_NEW,
    STATE_STARTED, STATE_STARTING, STATE_STOPPED, STATE_STOPPING,
};

const INPUT_RING_BLOCKS: usize = 8;
const OUTPUT_RING_BLOCKS: usize = 8;
const INPUT_CONSUMER_KEEP_BLOCKS: usize = 2;
const OUTPUT_TARGET_BLOCKS: usize = 2;
const OUTPUT_MAX_LIVE_BLOCKS: usize = 3;

#[pyclass(from_py_object)]
#[derive(Clone)]
pub struct AudioEngineConfig {
    #[pyo3(get)]
    pub sample_rate: i32,
    #[pyo3(get)]
    pub block_size: i32,
    #[pyo3(get)]
    pub input_device_id: i32,
    #[pyo3(get)]
    pub output_device_id: i32,
    #[pyo3(get)]
    pub input_channels: i32,
    #[pyo3(get)]
    pub output_channels: i32,
    #[pyo3(get)]
    pub latency: String,
}

#[pymethods]
impl AudioEngineConfig {
    #[new]
    #[pyo3(signature = (
        sample_rate,
        block_size,
        input_device_id,
        output_device_id,
        input_channels,
        output_channels,
        latency = None
    ))]
    fn new(
        sample_rate: i32,
        block_size: i32,
        input_device_id: i32,
        output_device_id: i32,
        input_channels: i32,
        output_channels: i32,
        latency: Option<String>,
    ) -> PyResult<Self> {
        if sample_rate <= 0 {
            return Err(runtime_error(format!(
                "Invalid sample_rate {sample_rate}; sample_rate must be > 0."
            )));
        }

        if block_size <= 0 {
            return Err(runtime_error(format!(
                "Invalid block_size {block_size}; block_size must be > 0."
            )));
        }

        if input_device_id < 0 {
            return Err(runtime_error(format!(
                "Invalid input_device_id {input_device_id}."
            )));
        }

        if output_device_id < 0 {
            return Err(runtime_error(format!(
                "Invalid output_device_id {output_device_id}."
            )));
        }

        if input_channels <= 0 {
            return Err(runtime_error(format!(
                "Invalid input_channels {input_channels}; input_channels must be > 0."
            )));
        }

        if output_channels <= 0 {
            return Err(runtime_error(format!(
                "Invalid output_channels {output_channels}; output_channels must be > 0."
            )));
        }

        let latency = latency.unwrap_or_else(|| "high".to_owned());
        if latency != "low" && latency != "high" {
            return Err(runtime_error(format!(
                "Invalid latency '{latency}'; expected 'low' or 'high'."
            )));
        }

        Ok(Self {
            sample_rate,
            block_size,
            input_device_id,
            output_device_id,
            input_channels,
            output_channels,
            latency,
        })
    }
}

#[pyclass]
pub struct AudioEngine {
    inner: Arc<EngineInner>,
}

#[pymethods]
impl AudioEngine {
    #[new]
    fn new(config: AudioEngineConfig) -> PyResult<Self> {
        Ok(Self {
            inner: Arc::new(EngineInner::new(config)?),
        })
    }

    fn start(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.start())
    }

    fn stop(&self, py: Python<'_>) -> PyResult<()> {
        py.detach(|| self.inner.stop())
    }

    fn is_active(&self) -> bool {
        self.inner.is_active()
    }

    fn read_input(
        &self,
        py: Python<'_>,
        timeout_ms: u64,
    ) -> PyResult<Option<Py<PyArray1<f32>>>> {
        let result = py.detach(|| self.inner.read_input_vec(timeout_ms));

        match result {
            Some(samples) => Ok(Some(PyArray1::from_vec(py, samples).unbind())),
            None => Ok(None),
        }
    }

    fn read_input_into(
        &self,
        py: Python<'_>,
        mut output: PyReadwriteArray1<'_, f32>,
        timeout_ms: u64,
    ) -> PyResult<bool> {
        let output = output.as_slice_mut().map_err(|_| {
            runtime_error("read_input_into() requires a contiguous 1D numpy.float32 array.")
        })?;

        if output.len() != self.inner.config.block_size as usize {
            return Err(runtime_error(format!(
                "read_input_into() buffer length must equal block_size {}; got {}.",
                self.inner.config.block_size,
                output.len()
            )));
        }

        Ok(py.detach(|| self.inner.read_input_into_slice(output, timeout_ms)))
    }

    fn input_queue_depth(&self) -> usize {
        self.inner.input_ring.available()
    }

    fn output_queue_depth(&self) -> usize {
        self.inner.output_ring.available()
    }

    fn target_output_queue_frames(&self) -> usize {
        self.inner.config.block_size as usize * OUTPUT_TARGET_BLOCKS
    }

    fn write_output(&self, samples: PyReadonlyArray1<'_, f32>) -> PyResult<bool> {
        let samples = samples.as_slice().map_err(|_| {
            runtime_error("write_output() requires a contiguous 1D numpy.float32 array.")
        })?;

        Ok(self.inner.write_output(samples))
    }

    fn stats(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        self.inner.stats(py)
    }
}

struct StreamPtr(*mut ffi::PaStream);

unsafe impl Send for StreamPtr {}

struct EngineInner {
    config: AudioEngineConfig,
    _runtime: PortAudioRuntime,

    input_ring: Arc<SpscSampleRing>,
    output_ring: Arc<SpscSampleRing>,

    input_consumer_lock: Mutex<()>,
    output_producer_lock: Mutex<()>,

    stats: Arc<EngineStats>,
    state: Arc<AtomicU8>,

    stream: Mutex<StreamPtr>,
    callback_data: Mutex<Option<Box<CallbackData>>>,
}

impl EngineInner {
    fn new(config: AudioEngineConfig) -> PyResult<Self> {
        let runtime = PortAudioRuntime::acquire()?;

        let block_size = config.block_size as usize;
        let input_capacity = block_size
            .checked_mul(INPUT_RING_BLOCKS)
            .ok_or_else(|| runtime_error("Input ring capacity overflow."))?;
        let output_capacity = block_size
            .checked_mul(OUTPUT_RING_BLOCKS)
            .ok_or_else(|| runtime_error("Output ring capacity overflow."))?;

        Ok(Self {
            config,
            _runtime: runtime,

            input_ring: Arc::new(SpscSampleRing::new(input_capacity)),
            output_ring: Arc::new(SpscSampleRing::new(output_capacity)),

            input_consumer_lock: Mutex::new(()),
            output_producer_lock: Mutex::new(()),

            stats: Arc::new(EngineStats::new()),
            state: Arc::new(AtomicU8::new(STATE_NEW)),

            stream: Mutex::new(StreamPtr(ptr::null_mut())),
            callback_data: Mutex::new(None),
        })
    }

    fn start(&self) -> PyResult<()> {
        if self
            .state
            .compare_exchange(
                STATE_NEW,
                STATE_STARTING,
                Ordering::AcqRel,
                Ordering::Acquire,
            )
            .is_err()
        {
            return Err(runtime_error(format!(
                "AudioEngine.start() can only be called once; current state is '{}'.",
                state_name(self.state.load(Ordering::Acquire))
            )));
        }

        let mut stream_guard = self
            .stream
            .lock()
            .map_err(|_| runtime_error("AudioEngine stream mutex was poisoned."))?;

        let low_latency = self.config.latency == "low";

        let input_params = build_stream_parameters(
            self.config.input_device_id,
            self.config.input_channels,
            StreamDirection::Input,
            low_latency,
        )
        .map_err(|err| {
            self.state.store(STATE_FAILED, Ordering::Release);
            err
        })?;

        let output_params = build_stream_parameters(
            self.config.output_device_id,
            self.config.output_channels,
            StreamDirection::Output,
            low_latency,
        )
        .map_err(|err| {
            self.state.store(STATE_FAILED, Ordering::Release);
            err
        })?;

        let mut callback_data = Box::new(CallbackData::new(
            self.config.sample_rate as f64,
            self.config.block_size as usize,
            self.config.input_channels as usize,
            self.config.output_channels as usize,
            Arc::clone(&self.input_ring),
            Arc::clone(&self.output_ring),
            Arc::clone(&self.stats),
            Arc::clone(&self.state),
        ));

        let user_data = (&mut *callback_data as *mut CallbackData).cast::<c_void>();

        {
            let mut callback_guard = self
                .callback_data
                .lock()
                .map_err(|_| runtime_error("AudioEngine callback-data mutex was poisoned."))?;
            *callback_guard = Some(callback_data);
        }

        let mut raw_stream: *mut ffi::PaStream = ptr::null_mut();

        let open_error = unsafe {
            ffi::Pa_OpenStream(
                &mut raw_stream,
                &input_params,
                &output_params,
                self.config.sample_rate as f64,
                self.config.block_size as c_ulong,
                ffi::PA_CLIP_OFF | ffi::PA_DITHER_OFF,
                Some(audio_callback),
                user_data,
            )
        };

        if open_error != ffi::PA_NO_ERROR {
            self.state.store(STATE_FAILED, Ordering::Release);
            let mut callback_guard = self
                .callback_data
                .lock()
                .map_err(|_| runtime_error("AudioEngine callback-data mutex was poisoned."))?;
            *callback_guard = None;
            return Err(runtime_error(format!(
                "Failed to open PortAudio stream: input_device={}, output_device={}, input_channels={}, output_channels={}, samplerate={}, block_size={}, dtype=float32: {}",
                self.config.input_device_id,
                self.config.output_device_id,
                self.config.input_channels,
                self.config.output_channels,
                self.config.sample_rate,
                self.config.block_size,
                ffi::error_text(open_error)
            )));
        }

        stream_guard.0 = raw_stream;

        self.state.store(STATE_STARTED, Ordering::Release);
        let start_error = unsafe { ffi::Pa_StartStream(raw_stream) };
        if start_error != ffi::PA_NO_ERROR {
            self.state.store(STATE_FAILED, Ordering::Release);

            let abort_error = unsafe { ffi::Pa_AbortStream(raw_stream) };
            let close_error = unsafe { ffi::Pa_CloseStream(raw_stream) };
            stream_guard.0 = ptr::null_mut();

            let mut callback_guard = self
                .callback_data
                .lock()
                .map_err(|_| runtime_error("AudioEngine callback-data mutex was poisoned."))?;
            *callback_guard = None;

            let mut details = vec![format!(
                "Failed to start PortAudio stream: input_device={}, output_device={}, input_channels={}, output_channels={}, samplerate={}, block_size={}, dtype=float32: {}",
                self.config.input_device_id,
                self.config.output_device_id,
                self.config.input_channels,
                self.config.output_channels,
                self.config.sample_rate,
                self.config.block_size,
                ffi::error_text(start_error)
            )];

            if abort_error != ffi::PA_NO_ERROR {
                details.push(format!(
                    "Abort after start failure also failed: {}",
                    ffi::error_text(abort_error)
                ));
            }

            if close_error != ffi::PA_NO_ERROR {
                details.push(format!(
                    "Close after start failure also failed: {}",
                    ffi::error_text(close_error)
                ));
            }

            return Err(runtime_error(details.join("\n")));
        }

        Ok(())
    }

    fn stop(&self) -> PyResult<()> {
        let previous_state = self.state.swap(STATE_STOPPING, Ordering::AcqRel);

        if previous_state == STATE_STOPPED {
            self.state.store(STATE_STOPPED, Ordering::Release);
            return Ok(());
        }

        let mut first_error: Option<String> = None;

        {
            let mut stream_guard = self
                .stream
                .lock()
                .map_err(|_| runtime_error("AudioEngine stream mutex was poisoned."))?;

            if !stream_guard.0.is_null() {
                let stream = stream_guard.0;

                if previous_state == STATE_FAILED {
                    let abort_error = unsafe { ffi::Pa_AbortStream(stream) };
                    if abort_error != ffi::PA_NO_ERROR {
                        first_error = Some(format!(
                            "Failed to abort PortAudio stream: {}",
                            ffi::error_text(abort_error)
                        ));
                    }
                } else {
                    let stop_error = unsafe { ffi::Pa_StopStream(stream) };
                    if stop_error != ffi::PA_NO_ERROR {
                        first_error = Some(format!(
                            "Failed to stop PortAudio stream: {}",
                            ffi::error_text(stop_error)
                        ));
                        let abort_error = unsafe { ffi::Pa_AbortStream(stream) };
                        if abort_error != ffi::PA_NO_ERROR {
                            first_error = Some(format!(
                                "{}\nAbort after stop failure also failed: {}",
                                first_error.unwrap_or_default(),
                                ffi::error_text(abort_error)
                            ));
                        }
                    }
                }

                let close_error = unsafe { ffi::Pa_CloseStream(stream) };
                if close_error != ffi::PA_NO_ERROR && first_error.is_none() {
                    first_error = Some(format!(
                        "Failed to close PortAudio stream: {}",
                        ffi::error_text(close_error)
                    ));
                }

                stream_guard.0 = ptr::null_mut();
            }
        }

        {
            let mut callback_guard = self
                .callback_data
                .lock()
                .map_err(|_| runtime_error("AudioEngine callback-data mutex was poisoned."))?;
            *callback_guard = None;
        }

        if previous_state == STATE_FAILED {
            self.state.store(STATE_FAILED, Ordering::Release);
        } else {
            self.state.store(STATE_STOPPED, Ordering::Release);
        }

        if let Some(message) = first_error {
            return Err(runtime_error(message));
        }

        Ok(())
    }

    fn is_active(&self) -> bool {
        let state = self.state.load(Ordering::Acquire);
        if state != STATE_STARTED {
            return false;
        }

        let Ok(stream_guard) = self.stream.lock() else {
            return false;
        };

        if stream_guard.0.is_null() {
            return false;
        }

        unsafe { ffi::Pa_IsStreamActive(stream_guard.0) == 1 }
    }

    fn read_input_vec(&self, timeout_ms: u64) -> Option<Vec<f32>> {
        let _guard = self.input_consumer_lock.lock().ok()?;

        let mut output = vec![0.0; self.config.block_size as usize];
        if self.read_input_locked(&mut output, timeout_ms) {
            Some(output)
        } else {
            None
        }
    }

    fn read_input_into_slice(&self, output: &mut [f32], timeout_ms: u64) -> bool {
        let Ok(_guard) = self.input_consumer_lock.lock() else {
            return false;
        };

        self.read_input_locked(output, timeout_ms)
    }

    fn read_input_locked(&self, output: &mut [f32], timeout_ms: u64) -> bool {
        let timeout = Duration::from_millis(timeout_ms);
        let deadline = Instant::now().checked_add(timeout);

        loop {
            self.trim_stale_input_for_live_latency();

            if self.input_ring.pop_exact_into(output) {
                self.stats
                    .set_input_queue_depth(self.input_ring.available());
                return true;
            }

            let state = self.state.load(Ordering::Acquire);
            if state == STATE_STOPPING || state == STATE_STOPPED || state == STATE_FAILED {
                return false;
            }

            if timeout_ms == 0 {
                return false;
            }

            if let Some(deadline) = deadline {
                let now = Instant::now();
                if now >= deadline {
                    return false;
                }

                let remaining = deadline.saturating_duration_since(now);
                thread::sleep(remaining.min(Duration::from_millis(1)));
            } else {
                thread::sleep(Duration::from_millis(1));
            }
        }
    }

    fn trim_stale_input_for_live_latency(&self) {
        let keep = self.config.block_size as usize * INPUT_CONSUMER_KEEP_BLOCKS;
        let available = self.input_ring.available();

        if available <= keep {
            self.stats.set_input_queue_depth(available);
            return;
        }

        let dropped = self.input_ring.discard_oldest(available - keep);
        self.stats.add_input_stale_drop(dropped);
        self.stats
            .set_input_queue_depth(self.input_ring.available());
    }

    fn write_output(&self, samples: &[f32]) -> bool {
        if samples.is_empty() {
            return true;
        }

        let state = self.state.load(Ordering::Acquire);
        if state == STATE_STOPPING || state == STATE_STOPPED || state == STATE_FAILED {
            return false;
        }

        if samples.len() > self.output_ring.capacity() {
            self.stats.add_output_drop(samples.len());
            return false;
        }

        let Ok(_guard) = self.output_producer_lock.lock() else {
            self.stats.add_output_drop(samples.len());
            return false;
        };

        if self.output_ring.push_slice(samples) {
            self.stats
                .set_output_queue_depth(self.output_ring.available());
            true
        } else {
            self.stats.add_output_drop(samples.len());
            self.stats
                .set_output_queue_depth(self.output_ring.available());
            false
        }
    }

    fn stats(&self, py: Python<'_>) -> PyResult<Py<PyDict>> {
        let out = PyDict::new(py);

        out.set_item(
            "callback_count",
            self.stats.callback_count.load(Ordering::Relaxed),
        )?;

        out.set_item(
            "input_ring_dropped_blocks",
            self.stats
                .input_ring_dropped_blocks
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "input_ring_dropped_frames",
            self.stats
                .input_ring_dropped_frames
                .load(Ordering::Relaxed),
        )?;

        out.set_item(
            "output_ring_dropped_blocks",
            self.stats
                .output_ring_dropped_blocks
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "output_ring_dropped_frames",
            self.stats
                .output_ring_dropped_frames
                .load(Ordering::Relaxed),
        )?;

        out.set_item(
            "output_underrun_blocks",
            self.stats.output_underrun_blocks.load(Ordering::Relaxed),
        )?;
        out.set_item(
            "output_underrun_frames",
            self.stats.output_underrun_frames.load(Ordering::Relaxed),
        )?;

        out.set_item(
            "input_stale_dropped_blocks",
            self.stats
                .input_stale_dropped_blocks
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "input_stale_dropped_frames",
            self.stats
                .input_stale_dropped_frames
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "output_stale_dropped_blocks",
            self.stats
                .output_stale_dropped_blocks
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "output_stale_dropped_frames",
            self.stats
                .output_stale_dropped_frames
                .load(Ordering::Relaxed),
        )?;

        out.set_item(
            "current_input_queue_frames",
            self.stats
                .current_input_queue_frames
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "max_input_queue_frames",
            self.stats.max_input_queue_frames.load(Ordering::Relaxed),
        )?;
        out.set_item(
            "current_output_queue_frames",
            self.stats
                .current_output_queue_frames
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "max_output_queue_frames",
            self.stats.max_output_queue_frames.load(Ordering::Relaxed),
        )?;
        out.set_item("target_output_queue_frames", self.config.block_size as usize * OUTPUT_TARGET_BLOCKS)?;
        out.set_item("max_live_output_queue_frames", self.config.block_size as usize * OUTPUT_MAX_LIVE_BLOCKS)?;
        out.set_item(
            "callback_input_frames",
            self.stats.callback_input_frames.load(Ordering::Relaxed),
        )?;
        out.set_item(
            "callback_output_frames",
            self.stats.callback_output_frames.load(Ordering::Relaxed),
        )?;
        out.set_item(
            "capture_render_drift_ppm",
            self.stats.capture_render_drift_ppm(self.config.sample_rate as f64),
        )?;

        out.set_item(
            "portaudio_input_underflow",
            self.stats
                .portaudio_input_underflow
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "portaudio_input_overflow",
            self.stats
                .portaudio_input_overflow
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "portaudio_output_underflow",
            self.stats
                .portaudio_output_underflow
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "portaudio_output_overflow",
            self.stats
                .portaudio_output_overflow
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "portaudio_priming_output",
            self.stats
                .portaudio_priming_output
                .load(Ordering::Relaxed),
        )?;

        out.set_item(
            "oversized_callback_count",
            self.stats
                .oversized_callback_count
                .load(Ordering::Relaxed),
        )?;
        out.set_item(
            "null_callback_count",
            self.stats.null_callback_count.load(Ordering::Relaxed),
        )?;

        out.set_item("fatal_error", self.stats.fatal_error_name())?;
        out.set_item(
            "state",
            state_name(self.state.load(Ordering::Acquire)),
        )?;

        let (input_latency, output_latency, actual_sample_rate) = self.stream_latency();
        out.set_item("actual_input_latency", input_latency)?;
        out.set_item("actual_output_latency", output_latency)?;
        out.set_item("actual_sample_rate", actual_sample_rate)?;

        Ok(out.unbind())
    }

    fn stream_latency(&self) -> (Option<f64>, Option<f64>, Option<f64>) {
        let Ok(stream_guard) = self.stream.lock() else {
            return (None, None, None);
        };

        if stream_guard.0.is_null() {
            return (None, None, None);
        }

        let info_ptr = unsafe { ffi::Pa_GetStreamInfo(stream_guard.0) };
        if info_ptr.is_null() {
            return (None, None, None);
        }

        let info = unsafe { *info_ptr };
        (
            Some(info.input_latency),
            Some(info.output_latency),
            Some(info.sample_rate),
        )
    }
}

impl Drop for EngineInner {
    fn drop(&mut self) {
        let _ = self.stop();
    }
}

struct CallbackData {
    sample_rate: f64,
    block_size: usize,
    input_channels: usize,
    output_channels: usize,
    max_output_queue_frames: usize,

    input_mono: Box<[f32]>,
    output_mono: Box<[f32]>,

    input_ring: Arc<SpscSampleRing>,
    output_ring: Arc<SpscSampleRing>,

    stats: Arc<EngineStats>,
    state: Arc<AtomicU8>,
}

impl CallbackData {
    fn new(
        sample_rate: f64,
        block_size: usize,
        input_channels: usize,
        output_channels: usize,
        input_ring: Arc<SpscSampleRing>,
        output_ring: Arc<SpscSampleRing>,
        stats: Arc<EngineStats>,
        state: Arc<AtomicU8>,
    ) -> Self {
        Self {
            sample_rate,
            block_size,
            input_channels,
            output_channels,
            max_output_queue_frames: block_size * OUTPUT_MAX_LIVE_BLOCKS,

            input_mono: vec![0.0; block_size].into_boxed_slice(),
            output_mono: vec![0.0; block_size].into_boxed_slice(),

            input_ring,
            output_ring,

            stats,
            state,
        }
    }
}

unsafe extern "C" fn audio_callback(
    input: *const c_void,
    output: *mut c_void,
    frame_count: c_ulong,
    time_info: *const ffi::PaStreamCallbackTimeInfo,
    status_flags: ffi::PaStreamCallbackFlags,
    user_data: *mut c_void,
) -> i32 {
    if user_data.is_null() {
        return ffi::PA_ABORT;
    }

    let data = &mut *(user_data.cast::<CallbackData>());
    data.stats.callback_count.fetch_add(1, Ordering::Relaxed);

    observe_status(data, status_flags);

    if output.is_null() || input.is_null() {
        data.stats
            .null_callback_count
            .fetch_add(1, Ordering::Relaxed);
        data.stats
            .fatal_error
            .store(FATAL_NULL_BUFFER, Ordering::Relaxed);
        data.state.store(STATE_FAILED, Ordering::Release);
        return ffi::PA_ABORT;
    }

    let frames = frame_count as usize;

    if frames > data.block_size {
        zero_output_direct(output.cast::<f32>(), frames, data.output_channels);
        data.stats
            .oversized_callback_count
            .fetch_add(1, Ordering::Relaxed);
        data.stats
            .fatal_error
            .store(FATAL_OVERSIZED_CALLBACK, Ordering::Relaxed);
        data.state.store(STATE_FAILED, Ordering::Release);
        return ffi::PA_ABORT;
    }

    match data.state.load(Ordering::Acquire) {
        STATE_STARTING => {
            zero_output_direct(output.cast::<f32>(), frames, data.output_channels);
            return ffi::PA_CONTINUE;
        }
        STATE_STARTED => {}
        STATE_STOPPING | STATE_STOPPED => {
            zero_output_direct(output.cast::<f32>(), frames, data.output_channels);
            return ffi::PA_COMPLETE;
        }
        STATE_FAILED => {
            zero_output_direct(output.cast::<f32>(), frames, data.output_channels);
            return ffi::PA_ABORT;
        }
        _ => {
            zero_output_direct(output.cast::<f32>(), frames, data.output_channels);
            return ffi::PA_ABORT;
        }
    }

    if !time_info.is_null() {
        let timing = *time_info;
        data.stats.record_callback_timing(
            frames,
            timing.input_buffer_adc_time,
            timing.output_buffer_dac_time,
        );
    } else {
        data.stats.record_callback_timing(frames, f64::NAN, f64::NAN);
    }

    downmix_to_mono(
        input.cast::<f32>(),
        data.input_mono.as_mut_ptr(),
        frames,
        data.input_channels,
    );

    let input_slice = std::slice::from_raw_parts(data.input_mono.as_ptr(), frames);
    if !data.input_ring.push_slice(input_slice) {
        data.stats
            .input_ring_dropped_blocks
            .fetch_add(1, Ordering::Relaxed);
        data.stats
            .input_ring_dropped_frames
            .fetch_add(frames as u64, Ordering::Relaxed);
    }
    data.stats.set_input_queue_depth(data.input_ring.available());

    let output_available = data.output_ring.available();
    if output_available > data.max_output_queue_frames {
        let dropped = data
            .output_ring
            .discard_oldest(output_available - data.max_output_queue_frames);
        data.stats.add_output_stale_drop(dropped);
    }

    let output_slice = std::slice::from_raw_parts_mut(data.output_mono.as_mut_ptr(), frames);
    let produced = data.output_ring.pop_into_zero_fill(output_slice);
    data.stats
        .set_output_queue_depth(data.output_ring.available());

    if produced < frames {
        data.stats
            .output_underrun_blocks
            .fetch_add(1, Ordering::Relaxed);
        data.stats
            .output_underrun_frames
            .fetch_add((frames - produced) as u64, Ordering::Relaxed);
    }

    write_mono_to_output(
        data.output_mono.as_ptr(),
        output.cast::<f32>(),
        frames,
        data.output_channels,
    );

    ffi::PA_CONTINUE
}

fn observe_status(data: &CallbackData, status_flags: ffi::PaStreamCallbackFlags) {
    if status_flags & ffi::PA_INPUT_UNDERFLOW != 0 {
        data.stats
            .portaudio_input_underflow
            .fetch_add(1, Ordering::Relaxed);
    }

    if status_flags & ffi::PA_INPUT_OVERFLOW != 0 {
        data.stats
            .portaudio_input_overflow
            .fetch_add(1, Ordering::Relaxed);
    }

    if status_flags & ffi::PA_OUTPUT_UNDERFLOW != 0 {
        data.stats
            .portaudio_output_underflow
            .fetch_add(1, Ordering::Relaxed);
    }

    if status_flags & ffi::PA_OUTPUT_OVERFLOW != 0 {
        data.stats
            .portaudio_output_overflow
            .fetch_add(1, Ordering::Relaxed);
    }

    if status_flags & ffi::PA_PRIMING_OUTPUT != 0 {
        data.stats
            .portaudio_priming_output
            .fetch_add(1, Ordering::Relaxed);
    }
}

unsafe fn downmix_to_mono(
    input: *const f32,
    output: *mut f32,
    frames: usize,
    channels: usize,
) {
    let scale = 1.0f32 / channels as f32;
    let mut frame = 0usize;

    while frame < frames {
        let mut sum = 0.0f32;
        let base = frame * channels;
        let mut channel = 0usize;

        while channel < channels {
            sum += *input.add(base + channel);
            channel += 1;
        }

        *output.add(frame) = sum * scale;
        frame += 1;
    }
}

unsafe fn write_mono_to_output(
    input: *const f32,
    output: *mut f32,
    frames: usize,
    channels: usize,
) {
    let mut frame = 0usize;

    while frame < frames {
        let sample = sanitize_sample(*input.add(frame));
        let base = frame * channels;
        let mut channel = 0usize;

        while channel < channels {
            *output.add(base + channel) = sample;
            channel += 1;
        }

        frame += 1;
    }
}

unsafe fn zero_output_direct(output: *mut f32, frames: usize, channels: usize) {
    let total = frames.saturating_mul(channels);
    let mut index = 0usize;

    while index < total {
        *output.add(index) = 0.0;
        index += 1;
    }
}

fn sanitize_sample(sample: f32) -> f32 {
    if !sample.is_finite() {
        return 0.0;
    }

    if sample > 1.0 {
        1.0
    } else if sample < -1.0 {
        -1.0
    } else {
        sample
    }
}
