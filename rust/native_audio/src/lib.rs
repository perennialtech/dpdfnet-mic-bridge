mod device;
mod engine;
mod errors;
mod platform_realtime;
mod portaudio_ffi;
mod ring;
mod runtime;
mod stats;

use pyo3::prelude::*;

#[pymodule]
fn _native_audio(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(device::query_audio_inventory, m)?)?;
    m.add_function(wrap_pyfunction!(device::check_input_settings, m)?)?;
    m.add_function(wrap_pyfunction!(device::check_output_settings, m)?)?;
    m.add_function(wrap_pyfunction!(
        device::check_full_duplex_stream_settings,
        m
    )?)?;
    m.add_class::<engine::AudioEngineConfig>()?;
    m.add_class::<engine::AudioEngine>()?;
    Ok(())
}
