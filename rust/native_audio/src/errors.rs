use pyo3::exceptions::PyRuntimeError;
use pyo3::PyErr;

pub(crate) fn runtime_error(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}
