pub(crate) const THREAD_RT_NOT_REQUESTED: u8 = 0;
pub(crate) const THREAD_RT_PENDING: u8 = 1;
pub(crate) const THREAD_RT_CONFIGURED: u8 = 2;
pub(crate) const THREAD_RT_FAILED: u8 = 3;
pub(crate) const THREAD_RT_UNSUPPORTED: u8 = 4;
pub(crate) const THREAD_RT_ATTEMPTING: u8 = 5;

pub(crate) struct ThreadRealtimeResult {
    pub(crate) status: u8,
    pub(crate) error_code: i32,
}

pub(crate) fn thread_realtime_status_name(status: u8) -> &'static str {
    match status {
        THREAD_RT_NOT_REQUESTED => "not_requested",
        THREAD_RT_PENDING => "pending",
        THREAD_RT_CONFIGURED => "configured",
        THREAD_RT_FAILED => "failed",
        THREAD_RT_UNSUPPORTED => "unsupported",
        THREAD_RT_ATTEMPTING => "attempting",
        _ => "unknown",
    }
}

#[cfg(target_os = "linux")]
pub(crate) fn configure_current_audio_thread(priority: i32) -> ThreadRealtimeResult {
    if !(1..=99).contains(&priority) {
        return ThreadRealtimeResult {
            status: THREAD_RT_FAILED,
            error_code: libc::EINVAL,
        };
    }

    let mut param = libc::sched_param {
        sched_priority: priority,
    };

    let rc = unsafe {
        libc::pthread_setschedparam(
            libc::pthread_self(),
            libc::SCHED_FIFO,
            &mut param,
        )
    };

    if rc == 0 {
        ThreadRealtimeResult {
            status: THREAD_RT_CONFIGURED,
            error_code: 0,
        }
    } else {
        ThreadRealtimeResult {
            status: THREAD_RT_FAILED,
            error_code: rc,
        }
    }
}

#[cfg(windows)]
pub(crate) fn configure_current_audio_thread(_priority: i32) -> ThreadRealtimeResult {
    use windows_sys::Win32::Foundation::{GetLastError, HANDLE};

    const AVRT_PRIORITY_HIGH: i32 = 2;
    static TASK_NAME: [u16; 10] = [
        b'P' as u16,
        b'r' as u16,
        b'o' as u16,
        b' ' as u16,
        b'A' as u16,
        b'u' as u16,
        b'd' as u16,
        b'i' as u16,
        b'o' as u16,
        0,
    ];

    #[link(name = "avrt")]
    extern "system" {
        fn AvSetMmThreadCharacteristicsW(task_name: *const u16, task_index: *mut u32) -> HANDLE;
        fn AvSetMmThreadPriority(avrt_handle: HANDLE, priority: i32) -> i32;
    }

    let mut task_index: u32 = 0;
    let handle = unsafe { AvSetMmThreadCharacteristicsW(TASK_NAME.as_ptr(), &mut task_index) };
    if handle.is_null() {
        return ThreadRealtimeResult {
            status: THREAD_RT_FAILED,
            error_code: unsafe { GetLastError() as i32 },
        };
    }

    let ok = unsafe { AvSetMmThreadPriority(handle, AVRT_PRIORITY_HIGH) };
    if ok == 0 {
        return ThreadRealtimeResult {
            status: THREAD_RT_FAILED,
            error_code: unsafe { GetLastError() as i32 },
        };
    }

    ThreadRealtimeResult {
        status: THREAD_RT_CONFIGURED,
        error_code: 0,
    }
}

#[cfg(not(any(target_os = "linux", windows)))]
pub(crate) fn configure_current_audio_thread(_priority: i32) -> ThreadRealtimeResult {
    ThreadRealtimeResult {
        status: THREAD_RT_UNSUPPORTED,
        error_code: 0,
    }
}
