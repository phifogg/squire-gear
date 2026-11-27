import Foundation

/// One device session at a time: battery poll, settings window and squire.py (SwiftBar plugin) must not
/// interleave their requests. Uses the same lock file as squire.device_lock on macOS (flock). Best effort:
/// if the file cannot be created, only the in-process lock applies.
enum DeviceLock {
    private static let inProcess = NSLock()

    static var path: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Application Support/squire-gear/squire-gear-device.lock")
    }

    static func run<T>(_ body: () -> T) -> T {
        inProcess.lock()
        defer { inProcess.unlock() }
        try? FileManager.default.createDirectory(at: path.deletingLastPathComponent(), withIntermediateDirectories: true)
        let fd = open(path.path, O_WRONLY | O_CREAT | O_CLOEXEC, 0o644)
        if fd >= 0 { flock(fd, LOCK_EX) }   // waits; a session takes about a second at most
        defer { if fd >= 0 { close(fd) } }  // closing releases the lock
        return body()
    }
}
