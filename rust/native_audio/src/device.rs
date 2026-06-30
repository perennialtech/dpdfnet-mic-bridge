use std::ptr;

use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};

use crate::errors::runtime_error;
use crate::portaudio_ffi as ffi;
use crate::runtime::PortAudioRuntime;

#[derive(Copy, Clone)]
pub(crate) enum StreamDirection {
    Input,
    Output,
}

pub(crate) fn build_stream_parameters(
    device_id: i32,
    channels: i32,
    direction: StreamDirection,
    low_latency: bool,
) -> PyResult<ffi::PaStreamParameters> {
    if device_id < 0 {
        return Err(runtime_error(format!("Invalid PortAudio device ID {device_id}.")));
    }

    if channels <= 0 {
        return Err(runtime_error(format!(
            "Invalid channel count {channels}; channel count must be > 0."
        )));
    }

    let info = device_info(device_id)?;

    let (max_channels, suggested_latency, direction_name) = match direction {
        StreamDirection::Input => {
            let latency = if low_latency {
                info.default_low_input_latency
            } else {
                info.default_high_input_latency
            };
            (info.max_input_channels, latency, "input")
        }
        StreamDirection::Output => {
            let latency = if low_latency {
                info.default_low_output_latency
            } else {
                info.default_high_output_latency
            };
            (info.max_output_channels, latency, "output")
        }
    };

    if channels > max_channels {
        return Err(runtime_error(format!(
            "Device {device_id} does not expose {channels} {direction_name} channels; max is {max_channels}."
        )));
    }

    Ok(ffi::PaStreamParameters {
        device: device_id,
        channel_count: channels,
        sample_format: ffi::PA_FLOAT32,
        suggested_latency,
        host_api_specific_stream_info: ptr::null_mut(),
    })
}

#[pyfunction]
pub fn query_audio_inventory(py: Python<'_>) -> PyResult<Py<PyDict>> {
    let _runtime = PortAudioRuntime::acquire()?;

    let hostapi_count = unsafe { ffi::Pa_GetHostApiCount() };
    if hostapi_count < 0 {
        return Err(runtime_error(format!(
            "Could not query PortAudio host APIs: {}",
            ffi::error_text(hostapi_count)
        )));
    }

    let hostapi_names = PyList::empty_bound(py);
    for hostapi_id in 0..hostapi_count {
        let info_ptr = unsafe { ffi::Pa_GetHostApiInfo(hostapi_id) };
        if info_ptr.is_null() {
            hostapi_names.append(format!("<unknown:{hostapi_id}>"))?;
            continue;
        }

        let info = unsafe { *info_ptr };
        hostapi_names.append(ffi::c_string(info.name))?;
    }

    let device_count = unsafe { ffi::Pa_GetDeviceCount() };
    if device_count < 0 {
        return Err(runtime_error(format!(
            "Could not query PortAudio devices: {}",
            ffi::error_text(device_count)
        )));
    }

    let devices = PyList::empty_bound(py);
    for device_id in 0..device_count {
        let info_ptr = unsafe { ffi::Pa_GetDeviceInfo(device_id) };
        if info_ptr.is_null() {
            continue;
        }

        let info = unsafe { *info_ptr };
        let item = PyDict::new_bound(py);

        item.set_item("id", device_id)?;
        item.set_item("name", ffi::c_string(info.name))?;
        item.set_item("hostapi", info.host_api)?;
        item.set_item("max_input_channels", info.max_input_channels)?;
        item.set_item("max_output_channels", info.max_output_channels)?;
        item.set_item("default_samplerate", info.default_sample_rate)?;
        item.set_item("default_low_input_latency", info.default_low_input_latency)?;
        item.set_item("default_high_input_latency", info.default_high_input_latency)?;
        item.set_item("default_low_output_latency", info.default_low_output_latency)?;
        item.set_item("default_high_output_latency", info.default_high_output_latency)?;

        devices.append(item)?;
    }

    let out = PyDict::new_bound(py);
    out.set_item("devices", devices)?;
    out.set_item("hostapi_names", hostapi_names)?;
    out.set_item("portaudio_version", ffi::version_text())?;
    Ok(out.unbind())
}

#[pyfunction]
#[pyo3(signature = (device_id, channels, samplerate, dtype = "float32"))]
pub fn check_input_settings(
    device_id: i32,
    channels: i32,
    samplerate: i32,
    dtype: &str,
) -> PyResult<()> {
    check_settings(
        device_id,
        channels,
        samplerate,
        dtype,
        StreamDirection::Input,
    )
}

#[pyfunction]
#[pyo3(signature = (device_id, channels, samplerate, dtype = "float32"))]
pub fn check_output_settings(
    device_id: i32,
    channels: i32,
    samplerate: i32,
    dtype: &str,
) -> PyResult<()> {
    check_settings(
        device_id,
        channels,
        samplerate,
        dtype,
        StreamDirection::Output,
    )
}

fn check_settings(
    device_id: i32,
    channels: i32,
    samplerate: i32,
    dtype: &str,
    direction: StreamDirection,
) -> PyResult<()> {
    let _runtime = PortAudioRuntime::acquire()?;

    if dtype != "float32" {
        return Err(runtime_error(format!(
            "Unsupported dtype '{dtype}'; only float32 is supported by the native audio engine."
        )));
    }

    if samplerate <= 0 {
        return Err(runtime_error(format!(
            "Invalid sample rate {samplerate}; sample rate must be > 0."
        )));
    }

    let params = build_stream_parameters(device_id, channels, direction, false)?;

    let (input_params, output_params, direction_name) = match direction {
        StreamDirection::Input => (&params as *const _, ptr::null(), "input"),
        StreamDirection::Output => (ptr::null(), &params as *const _, "output"),
    };

    let error =
        unsafe { ffi::Pa_IsFormatSupported(input_params, output_params, samplerate as f64) };

    if error == ffi::PA_NO_ERROR {
        return Ok(());
    }

    Err(runtime_error(format!(
        "PortAudio rejected {direction_name} settings: device={device_id}, channels={channels}, samplerate={samplerate}, dtype={dtype}: {}",
        ffi::error_text(error)
    )))
}

fn device_info(device_id: i32) -> PyResult<ffi::PaDeviceInfo> {
    let info_ptr = unsafe { ffi::Pa_GetDeviceInfo(device_id) };
    if info_ptr.is_null() {
        return Err(runtime_error(format!(
            "Invalid PortAudio device ID {device_id}."
        )));
    }

    Ok(unsafe { *info_ptr })
}
