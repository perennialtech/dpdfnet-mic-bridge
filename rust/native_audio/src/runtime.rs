use std::sync::Mutex;

use pyo3::PyResult;

use crate::errors::runtime_error;
use crate::portaudio_ffi as ffi;

struct RuntimeState {
    refs: usize,
}

static PORTAUDIO_RUNTIME: Mutex<RuntimeState> = Mutex::new(RuntimeState { refs: 0 });

pub(crate) struct PortAudioRuntime;

impl PortAudioRuntime {
    pub(crate) fn acquire() -> PyResult<Self> {
        let mut state = PORTAUDIO_RUNTIME
            .lock()
            .map_err(|_| runtime_error("PortAudio runtime mutex was poisoned."))?;

        if state.refs == 0 {
            let error = unsafe { ffi::Pa_Initialize() };
            if error != ffi::PA_NO_ERROR {
                return Err(runtime_error(format!(
                    "Could not initialize PortAudio: {}",
                    ffi::error_text(error)
                )));
            }
        }

        state.refs += 1;
        Ok(Self)
    }
}

impl Drop for PortAudioRuntime {
    fn drop(&mut self) {
        let Ok(mut state) = PORTAUDIO_RUNTIME.lock() else {
            return;
        };

        if state.refs == 0 {
            return;
        }

        state.refs -= 1;
        if state.refs == 0 {
            let _ = unsafe { ffi::Pa_Terminate() };
        }
    }
}
