use std::ffi::CStr;
use std::os::raw::{c_char, c_double, c_int, c_ulong, c_void};

pub(crate) type PaError = c_int;
pub(crate) type PaDeviceIndex = c_int;
pub(crate) type PaHostApiIndex = c_int;
pub(crate) type PaHostApiTypeId = c_int;
pub(crate) type PaSampleFormat = c_ulong;
pub(crate) type PaStreamFlags = c_ulong;
pub(crate) type PaStreamCallbackFlags = c_ulong;
pub(crate) type PaTime = c_double;

#[repr(C)]
pub(crate) struct PaStream {
    _private: [u8; 0],
}

#[repr(C)]
#[derive(Copy, Clone)]
pub(crate) struct PaStreamParameters {
    pub(crate) device: PaDeviceIndex,
    pub(crate) channel_count: c_int,
    pub(crate) sample_format: PaSampleFormat,
    pub(crate) suggested_latency: PaTime,
    pub(crate) host_api_specific_stream_info: *mut c_void,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub(crate) struct PaDeviceInfo {
    pub(crate) struct_version: c_int,
    pub(crate) name: *const c_char,
    pub(crate) host_api: PaHostApiIndex,
    pub(crate) max_input_channels: c_int,
    pub(crate) max_output_channels: c_int,
    pub(crate) default_low_input_latency: PaTime,
    pub(crate) default_low_output_latency: PaTime,
    pub(crate) default_high_input_latency: PaTime,
    pub(crate) default_high_output_latency: PaTime,
    pub(crate) default_sample_rate: c_double,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub(crate) struct PaHostApiInfo {
    pub(crate) struct_version: c_int,
    pub(crate) type_id: PaHostApiTypeId,
    pub(crate) name: *const c_char,
    pub(crate) device_count: c_int,
    pub(crate) default_input_device: PaDeviceIndex,
    pub(crate) default_output_device: PaDeviceIndex,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub(crate) struct PaStreamCallbackTimeInfo {
    pub(crate) input_buffer_adc_time: PaTime,
    pub(crate) current_time: PaTime,
    pub(crate) output_buffer_dac_time: PaTime,
}

#[repr(C)]
#[derive(Copy, Clone)]
pub(crate) struct PaStreamInfo {
    pub(crate) struct_version: c_int,
    pub(crate) input_latency: PaTime,
    pub(crate) output_latency: PaTime,
    pub(crate) sample_rate: c_double,
}

pub(crate) type PaStreamCallback = Option<
    unsafe extern "C" fn(
        input: *const c_void,
        output: *mut c_void,
        frame_count: c_ulong,
        time_info: *const PaStreamCallbackTimeInfo,
        status_flags: PaStreamCallbackFlags,
        user_data: *mut c_void,
    ) -> c_int,
>;

pub(crate) const PA_NO_ERROR: PaError = 0;

pub(crate) const PA_FLOAT32: PaSampleFormat = 0x0000_0001;

pub(crate) const PA_CLIP_OFF: PaStreamFlags = 0x0000_0001;
pub(crate) const PA_DITHER_OFF: PaStreamFlags = 0x0000_0002;

pub(crate) const PA_CONTINUE: c_int = 0;
pub(crate) const PA_COMPLETE: c_int = 1;
pub(crate) const PA_ABORT: c_int = 2;

pub(crate) const PA_INPUT_UNDERFLOW: PaStreamCallbackFlags = 0x0000_0001;
pub(crate) const PA_INPUT_OVERFLOW: PaStreamCallbackFlags = 0x0000_0002;
pub(crate) const PA_OUTPUT_UNDERFLOW: PaStreamCallbackFlags = 0x0000_0004;
pub(crate) const PA_OUTPUT_OVERFLOW: PaStreamCallbackFlags = 0x0000_0008;
pub(crate) const PA_PRIMING_OUTPUT: PaStreamCallbackFlags = 0x0000_0010;

extern "C" {
    pub(crate) fn Pa_Initialize() -> PaError;
    pub(crate) fn Pa_Terminate() -> PaError;
    pub(crate) fn Pa_GetVersionText() -> *const c_char;

    pub(crate) fn Pa_GetDeviceCount() -> PaDeviceIndex;
    pub(crate) fn Pa_GetDeviceInfo(device: PaDeviceIndex) -> *const PaDeviceInfo;

    pub(crate) fn Pa_GetHostApiCount() -> PaHostApiIndex;
    pub(crate) fn Pa_GetHostApiInfo(host_api: PaHostApiIndex) -> *const PaHostApiInfo;

    pub(crate) fn Pa_IsFormatSupported(
        input_parameters: *const PaStreamParameters,
        output_parameters: *const PaStreamParameters,
        sample_rate: c_double,
    ) -> PaError;

    pub(crate) fn Pa_OpenStream(
        stream: *mut *mut PaStream,
        input_parameters: *const PaStreamParameters,
        output_parameters: *const PaStreamParameters,
        sample_rate: c_double,
        frames_per_buffer: c_ulong,
        stream_flags: PaStreamFlags,
        stream_callback: PaStreamCallback,
        user_data: *mut c_void,
    ) -> PaError;

    pub(crate) fn Pa_StartStream(stream: *mut PaStream) -> PaError;
    pub(crate) fn Pa_StopStream(stream: *mut PaStream) -> PaError;
    pub(crate) fn Pa_AbortStream(stream: *mut PaStream) -> PaError;
    pub(crate) fn Pa_CloseStream(stream: *mut PaStream) -> PaError;
    pub(crate) fn Pa_IsStreamActive(stream: *mut PaStream) -> PaError;
    pub(crate) fn Pa_GetStreamInfo(stream: *mut PaStream) -> *const PaStreamInfo;

    pub(crate) fn Pa_GetErrorText(error_code: PaError) -> *const c_char;
}

pub(crate) fn c_string(ptr: *const c_char) -> String {
    if ptr.is_null() {
        return "<null>".to_owned();
    }

    unsafe { CStr::from_ptr(ptr).to_string_lossy().into_owned() }
}

pub(crate) fn error_text(error: PaError) -> String {
    unsafe { c_string(Pa_GetErrorText(error)) }
}

pub(crate) fn version_text() -> String {
    unsafe { c_string(Pa_GetVersionText()) }
}
