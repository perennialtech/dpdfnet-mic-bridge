//! Query Windows audio endpoint properties through PortAudio's WASAPI IMMDevice.
//! The returned IMMDevice is borrowed from PortAudio and is valid while the
//! caller holds PortAudioRuntime.
use std::ffi::c_void;
use std::ptr;

use windows_sys::core::{IUnknown_Vtbl, GUID};
use windows_sys::Win32::Foundation::PROPERTYKEY;

#[repr(C)]
struct ImmDeviceVtbl {
    base: IUnknown_Vtbl,
    activate: usize,
    open_property_store: unsafe extern "system" fn(*mut c_void, u32, *mut *mut c_void) -> i32,
    get_id: usize,
    get_state: usize,
}

#[repr(C)]
struct PropertyStoreVtbl {
    base: IUnknown_Vtbl,
    get_count: usize,
    get_at: usize,
    get_value: unsafe extern "system" fn(*mut c_void, *const PROPERTYKEY, *mut PropVariant) -> i32,
    set_value: usize,
    commit: usize,
}

// PROPVARIANT's VT_LPWSTR / VT_CLSID payload is a pointer at offset 8 on
// Windows x64. The structure is 24 bytes, including its trailing union data.
#[repr(C)]
#[derive(Default)]
struct PropVariant {
    vt: u16,
    reserved: [u16; 3],
    value: usize,
    trailing: usize,
}

#[link(name = "ole32")]
extern "system" {
    fn PropVariantClear(value: *mut PropVariant) -> i32;
}

extern "C" {
    fn PaWasapi_GetIMMDevice(device: i32, imm_device: *mut *mut c_void) -> i32;
}

const PKEY_CONTAINER: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0x8c7ed206_3f8a_4827_b3ab_ae9e1faefc6c),
    pid: 2,
};
const PKEY_DESCRIPTION: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0xa45c254e_df1c_4efd_8020_67d146a850e0),
    pid: 2,
};
const PKEY_MANUFACTURER: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0xa45c254e_df1c_4efd_8020_67d146a850e0),
    pid: 13,
};

pub(crate) struct Metadata {
    pub(crate) container: Option<String>,
    pub(crate) description: Option<String>,
    pub(crate) manufacturer: Option<String>,
}

unsafe fn property(store: *mut c_void, key: &PROPERTYKEY) -> Option<String> {
    let vtbl = &**(store as *const *const PropertyStoreVtbl);
    let mut value = PropVariant::default();
    if (vtbl.get_value)(store, key, &mut value) < 0 {
        return None;
    }
    let result = match value.vt {
        31 if value.value != 0 => {
            let ptr = value.value as *const u16;
            let mut length = 0;
            while length < 4096 && *ptr.add(length) != 0 {
                length += 1;
            }
            if length == 4096 {
                None
            } else {
                Some(String::from_utf16_lossy(std::slice::from_raw_parts(
                    ptr, length,
                )))
            }
        }
        72 if value.value != 0 => {
            let guid = &*(value.value as *const GUID);
            Some(format!(
                "{:08x}-{:04x}-{:04x}-{:02x}{:02x}-{:02x}{:02x}{:02x}{:02x}{:02x}{:02x}",
                guid.data1,
                guid.data2,
                guid.data3,
                guid.data4[0],
                guid.data4[1],
                guid.data4[2],
                guid.data4[3],
                guid.data4[4],
                guid.data4[5],
                guid.data4[6],
                guid.data4[7],
            ))
        }
        _ => None,
    };
    PropVariantClear(&mut value);
    result
}

pub(crate) fn device_metadata(device_index: i32) -> Option<Metadata> {
    unsafe {
        let mut device = ptr::null_mut();
        if PaWasapi_GetIMMDevice(device_index, &mut device) != 0 || device.is_null() {
            return None;
        }
        let vtbl = &**(device as *const *const ImmDeviceVtbl);
        let mut store = ptr::null_mut();
        if (vtbl.open_property_store)(device, 0, &mut store) < 0 || store.is_null() {
            return None;
        }
        let metadata = Metadata {
            container: property(store, &PKEY_CONTAINER),
            description: property(store, &PKEY_DESCRIPTION),
            manufacturer: property(store, &PKEY_MANUFACTURER),
        };
        let store_vtbl = &**(store as *const *const PropertyStoreVtbl);
        (store_vtbl.base.Release)(store);
        Some(metadata)
    }
}
