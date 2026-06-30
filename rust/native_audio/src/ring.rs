use std::cell::UnsafeCell;
use std::ptr;
use std::sync::atomic::{AtomicUsize, Ordering};

pub(crate) struct SpscSampleRing {
    buffer: UnsafeCell<Box<[f32]>>,
    capacity: usize,
    read: AtomicUsize,
    write: AtomicUsize,
}

unsafe impl Send for SpscSampleRing {}
unsafe impl Sync for SpscSampleRing {}

impl SpscSampleRing {
    pub(crate) fn new(capacity: usize) -> Self {
        assert!(capacity > 0);

        Self {
            buffer: UnsafeCell::new(vec![0.0; capacity].into_boxed_slice()),
            capacity,
            read: AtomicUsize::new(0),
            write: AtomicUsize::new(0),
        }
    }

    pub(crate) fn capacity(&self) -> usize {
        self.capacity
    }

    pub(crate) fn available(&self) -> usize {
        let read = self.read.load(Ordering::Acquire);
        let write = self.write.load(Ordering::Acquire);
        write.wrapping_sub(read)
    }

    pub(crate) fn space(&self) -> usize {
        self.capacity - self.available()
    }

    /// Discard the oldest queued frames.
    ///
    /// This must only be called by the consumer side of the SPSC queue. That
    /// preserves the ring's ownership model while allowing latency recovery by
    /// dropping stale audio in favor of newer audio.
    pub(crate) fn discard_oldest(&self, requested: usize) -> usize {
        if requested == 0 {
            return 0;
        }

        let read = self.read.load(Ordering::Relaxed);
        let write = self.write.load(Ordering::Acquire);
        let available = write.wrapping_sub(read);
        let count = requested.min(available);

        if count > 0 {
            self.read.store(read.wrapping_add(count), Ordering::Release);
        }

        count
    }

    pub(crate) fn push_slice(&self, input: &[f32]) -> bool {
        let count = input.len();
        if count == 0 {
            return true;
        }

        let read = self.read.load(Ordering::Acquire);
        let write = self.write.load(Ordering::Relaxed);
        let available = write.wrapping_sub(read);
        if count > self.capacity - available {
            return false;
        }

        unsafe {
            self.copy_into_ring(write, input.as_ptr(), count);
        }

        self.write.store(write.wrapping_add(count), Ordering::Release);
        true
    }

    pub(crate) fn pop_exact_into(&self, output: &mut [f32]) -> bool {
        let count = output.len();
        if count == 0 {
            return true;
        }

        let read = self.read.load(Ordering::Relaxed);
        let write = self.write.load(Ordering::Acquire);
        if write.wrapping_sub(read) < count {
            return false;
        }

        unsafe {
            self.copy_from_ring(read, output.as_mut_ptr(), count);
        }

        self.read.store(read.wrapping_add(count), Ordering::Release);
        true
    }

    pub(crate) fn pop_into_zero_fill(&self, output: &mut [f32]) -> usize {
        let requested = output.len();
        if requested == 0 {
            return 0;
        }

        let read = self.read.load(Ordering::Relaxed);
        let write = self.write.load(Ordering::Acquire);
        let available = write.wrapping_sub(read);
        let count = requested.min(available);

        unsafe {
            if count > 0 {
                self.copy_from_ring(read, output.as_mut_ptr(), count);
                self.read.store(read.wrapping_add(count), Ordering::Release);
            }

            if count < requested {
                ptr::write_bytes(output.as_mut_ptr().add(count), 0, requested - count);
            }
        }

        count
    }

    unsafe fn copy_into_ring(&self, write_cursor: usize, src: *const f32, count: usize) {
        let buffer = (*self.buffer.get()).as_mut_ptr();
        let start = write_cursor % self.capacity;
        let first = count.min(self.capacity - start);
        let second = count - first;

        ptr::copy_nonoverlapping(src, buffer.add(start), first);

        if second > 0 {
            ptr::copy_nonoverlapping(src.add(first), buffer, second);
        }
    }

    unsafe fn copy_from_ring(&self, read_cursor: usize, dst: *mut f32, count: usize) {
        let buffer = (*self.buffer.get()).as_ptr();
        let start = read_cursor % self.capacity;
        let first = count.min(self.capacity - start);
        let second = count - first;

        ptr::copy_nonoverlapping(buffer.add(start), dst, first);

        if second > 0 {
            ptr::copy_nonoverlapping(buffer, dst.add(first), second);
        }
    }
}
