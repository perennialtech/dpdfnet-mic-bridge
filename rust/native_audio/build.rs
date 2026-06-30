use std::env;

fn main() {
    println!("cargo:rerun-if-env-changed=PORTAUDIO_LIB_DIR");
    println!("cargo:rerun-if-env-changed=PORTAUDIO_LIB_NAME");

    if let Ok(dir) = env::var("PORTAUDIO_LIB_DIR") {
        println!("cargo:rustc-link-search=native={dir}");
    }

    if let Ok(name) = env::var("PORTAUDIO_LIB_NAME") {
        println!("cargo:rustc-link-lib={name}");
        return;
    }

    if pkg_config::Config::new().probe("portaudio-2.0").is_ok() {
        return;
    }

    if pkg_config::Config::new().probe("portaudio").is_ok() {
        return;
    }

    if cfg!(target_os = "windows") {
        if vcpkg::find_package("portaudio").is_ok() {
            return;
        }
    }

    println!("cargo:rustc-link-lib=portaudio");
}
