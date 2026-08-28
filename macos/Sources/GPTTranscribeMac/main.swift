import AppKit
import AVFoundation
import Carbon
import CoreGraphics
import Foundation
import ApplicationServices
import ServiceManagement
import Security
import UserNotifications

private let appName = "GPT Transcribe"
private let appVersion = "0.3.7"
private let model = "gpt-realtime-transcribe"
private let transcriptionURL = URL(string: "https://api.openai.com/v1/audio/transcriptions")!
private let defaultHotkey = "ctrl+shift+space"
private let defaultMaxRecordingSeconds = 90
private let failedRecordingFilename = "failed-recording.wav"

// MARK: - Local storage and logging

func appSupportDirectory() -> URL {
    let base = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
        ?? URL(fileURLWithPath: NSHomeDirectory()).appendingPathComponent("Library/Application Support")
    return base.appendingPathComponent(appName, isDirectory: true)
}

func failedRecordingURL(in directory: URL = appSupportDirectory()) -> URL {
    directory.appendingPathComponent(failedRecordingFilename, isDirectory: false)
}

@discardableResult
func saveFailedRecording(_ audio: Data, in directory: URL = appSupportDirectory()) throws -> URL {
    let fileManager = FileManager.default
    try fileManager.createDirectory(at: directory, withIntermediateDirectories: true)
    let url = failedRecordingURL(in: directory)
    try audio.write(to: url, options: [.atomic])
    return url
}

func deleteFailedRecording(at url: URL = failedRecordingURL()) throws {
    let fileManager = FileManager.default
    guard fileManager.fileExists(atPath: url.path) else { return }
    try fileManager.removeItem(at: url)
}

final class AppLogger {
    static let shared = AppLogger()

    private let queue = DispatchQueue(label: "com.gpttranscribe.logger")
    private let logURL: URL
    private let formatter: DateFormatter

    private init() {
        let directory = appSupportDirectory()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        logURL = directory.appendingPathComponent("app.log")
        formatter = DateFormatter()
        formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    }

    func info(_ message: String) {
        write("INFO", message)
    }

    func error(_ message: String) {
        write("ERROR", message)
    }

    private func write(_ level: String, _ message: String) {
        queue.async { [logURL, formatter] in
            let line = "\(formatter.string(from: Date())) \(level) \(message)\n"
            guard let data = line.data(using: .utf8) else { return }
            if FileManager.default.fileExists(atPath: logURL.path) {
                if let handle = try? FileHandle(forWritingTo: logURL) {
                    defer { try? handle.close() }
                    _ = try? handle.seekToEnd()
                    try? handle.write(contentsOf: data)
                }
            } else {
                try? data.write(to: logURL, options: .atomic)
            }
        }
    }
}

func parseSettingList(_ value: String) -> [String] {
    value
        .replacingOccurrences(of: ",", with: "\n")
        .split(whereSeparator: { $0 == "\n" || $0 == "\r" })
        .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        .filter { !$0.isEmpty }
}

struct AppConfig {
    var hotkey: String = defaultHotkey
    var prompt: String = ""
    var keywords: [String] = []
    var languages: [String] = []
    var maxRecordingSeconds: Int = defaultMaxRecordingSeconds
    var launchAtLogin: Bool = false

    init(
        hotkey: String = defaultHotkey,
        language: String = "",
        prompt: String = "",
        keywords: [String] = [],
        languages: [String] = [],
        maxRecordingSeconds: Int = defaultMaxRecordingSeconds,
        launchAtLogin: Bool = false
    ) {
        self.hotkey = hotkey
        self.prompt = prompt
        self.keywords = keywords
        self.languages = languages.isEmpty ? parseSettingList(language) : languages
        self.maxRecordingSeconds = maxRecordingSeconds
        self.launchAtLogin = launchAtLogin
        normalize()
    }

    init(defaults: UserDefaults = .standard) {
        self.hotkey = defaults.string(forKey: "hotkey") ?? defaultHotkey
        self.prompt = defaults.string(forKey: "prompt") ?? ""
        self.keywords = defaults.stringArray(forKey: "keywords") ?? []
        if let storedLanguages = defaults.stringArray(forKey: "languages") {
            self.languages = storedLanguages
        } else {
            self.languages = parseSettingList(defaults.string(forKey: "language") ?? "")
        }
        let storedSeconds = defaults.object(forKey: "maxRecordingSeconds") as? NSNumber
        self.maxRecordingSeconds = storedSeconds?.intValue ?? defaultMaxRecordingSeconds
        self.launchAtLogin = defaults.object(forKey: "launchAtLogin") as? Bool ?? false
        normalize()
    }

    var language: String {
        get { languages.first ?? "" }
        set { languages = parseSettingList(newValue) }
    }

    mutating func normalize() {
        hotkey = hotkey.trimmingCharacters(in: .whitespacesAndNewlines).lowercased()
        if hotkey.isEmpty { hotkey = defaultHotkey }
        prompt = prompt.trimmingCharacters(in: .whitespacesAndNewlines)
        keywords = parseSettingList(keywords.joined(separator: "\n"))
        languages = parseSettingList(languages.joined(separator: "\n"))
        maxRecordingSeconds = maxRecordingSeconds == 0 ? 0 : min(180, max(5, maxRecordingSeconds))
    }

    func save(to defaults: UserDefaults = .standard) {
        defaults.set(hotkey, forKey: "hotkey")
        defaults.set(prompt, forKey: "prompt")
        defaults.set(keywords, forKey: "keywords")
        defaults.set(languages, forKey: "languages")
        // Keep the legacy key populated so older builds can still read the
        // first configured language if the user downgrades.
        defaults.set(language, forKey: "language")
        defaults.set(maxRecordingSeconds, forKey: "maxRecordingSeconds")
        defaults.set(launchAtLogin, forKey: "launchAtLogin")
    }
}

func parseMaxRecordingSeconds(_ value: String) -> Int {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    return trimmed.isEmpty ? 0 : (Int(trimmed) ?? defaultMaxRecordingSeconds)
}

enum KeychainStore {
    private static let service = "com.gpttranscribe.macos"
    private static let account = "OpenAI API Key"

    static func read() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecMatchLimit as String: kSecMatchLimitOne,
            kSecReturnData as String: true,
        ]
        var result: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &result)
        guard status == errSecSuccess, let data = result as? Data else { return nil }
        return String(data: data, encoding: .utf8)?.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    static func save(_ value: String) throws {
        let data = Data(value.utf8)
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let attributes: [String: Any] = [kSecValueData as String: data]
        let updateStatus = SecItemUpdate(query as CFDictionary, attributes as CFDictionary)
        if updateStatus == errSecItemNotFound {
            var addQuery = query
            addQuery[kSecValueData as String] = data
            let addStatus = SecItemAdd(addQuery as CFDictionary, nil)
            guard addStatus == errSecSuccess else { throw KeychainError(status: addStatus) }
        } else if updateStatus != errSecSuccess {
            throw KeychainError(status: updateStatus)
        }
    }

    static func remove() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainError(status: status)
        }
    }

    static var configuredValue: String? {
        if let environmentValue = ProcessInfo.processInfo.environment["OPENAI_API_KEY"]?.trimmingCharacters(in: .whitespacesAndNewlines), !environmentValue.isEmpty {
            return environmentValue
        }
        return read()
    }
}

struct KeychainError: LocalizedError {
    let status: OSStatus

    var errorDescription: String? {
        "Could not update the macOS Keychain (status \(status))."
    }
}

// MARK: - Hotkey parsing and registration

struct ParsedHotkey: Equatable {
    let modifiers: UInt32
    let keyCode: UInt32
}

enum HotkeyError: LocalizedError {
    case missingKey
    case unknownModifier(String)
    case unknownKey(String)
    case registrationFailed(String, OSStatus)

    var errorDescription: String? {
        switch self {
        case .missingKey:
            return "A hotkey needs at least one modifier and one key."
        case .unknownModifier(let modifier):
            return "Unknown hotkey modifier: \(modifier)."
        case .unknownKey(let key):
            return "Unknown hotkey key: \(key)."
        case .registrationFailed(let hotkey, _):
            return "Hotkey unavailable: \(hotkey). Choose another combination in Settings."
        }
    }
}

private let keyCodes: [String: UInt32] = [
    "a": 0, "s": 1, "d": 2, "f": 3, "h": 4, "g": 5, "z": 6, "x": 7, "c": 8, "v": 9,
    "b": 11, "q": 12, "w": 13, "e": 14, "r": 15, "y": 16, "t": 17, "1": 18, "2": 19,
    "3": 20, "4": 21, "6": 22, "5": 23, "=": 24, "9": 25, "7": 26, "-": 27, "8": 28,
    "0": 29, "]": 30, "o": 31, "u": 32, "[": 33, "i": 34, "p": 35, "l": 37, "j": 38,
    "'": 39, "k": 40, ";": 41, "\\": 42, ",": 43, "/": 44, "n": 45, "m": 46, ".": 47,
    "`": 50, "enter": 36, "return": 36, "tab": 48, "space": 49, "delete": 51, "escape": 53,
    "esc": 53, "command": 55, "cmd": 55, "shiftkey": 56, "capslock": 57, "option": 58,
    "alt": 58, "controlkey": 59, "ctrlkey": 59, "rightshift": 60, "rightoption": 61,
    "rightcontrol": 62, "function": 63, "f1": 122, "f2": 120, "f3": 99, "f4": 118,
    "f5": 96, "f6": 97, "f7": 98, "f8": 100, "f9": 101, "f10": 109, "f11": 103,
    "f12": 111,
]

func parseHotkey(_ value: String) throws -> ParsedHotkey {
    let parts = value.split(separator: "+").map { $0.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() }.filter { !$0.isEmpty }
    guard parts.count >= 2 else { throw HotkeyError.missingKey }

    var modifiers: UInt32 = 0
    for part in parts.dropLast() {
        switch part {
        case "cmd", "command": modifiers |= UInt32(cmdKey)
        case "ctrl", "control": modifiers |= UInt32(controlKey)
        case "shift": modifiers |= UInt32(shiftKey)
        case "alt", "option": modifiers |= UInt32(optionKey)
        default: throw HotkeyError.unknownModifier(part)
        }
    }
    guard modifiers != 0 else { throw HotkeyError.missingKey }

    let key = parts[parts.count - 1]
    if let keyCode = keyCodes[key] {
        return ParsedHotkey(modifiers: modifiers, keyCode: keyCode)
    }
    throw HotkeyError.unknownKey(key)
}

final class HotKeyManager {
    private var eventHandler: EventHandlerRef?
    private var hotKey: EventHotKeyRef?
    private var callback: (() -> Void)?
    private var hotKeyID = EventHotKeyID(signature: OSType(0x47505452), id: 1)

    func register(_ value: String, callback: @escaping () -> Void) throws {
        let parsed = try parseHotkey(value)
        unregister()
        self.callback = callback

        let eventSpec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let userData = Unmanaged.passUnretained(self).toOpaque()
        let handler: EventHandlerUPP = { _, event, userData in
            guard let event, let userData else { return noErr }
            var pressedID = EventHotKeyID()
            let size = MemoryLayout<EventHotKeyID>.size
            let status = GetEventParameter(
                event,
                EventParamName(kEventParamDirectObject),
                EventParamType(typeEventHotKeyID),
                nil,
                size,
                nil,
                &pressedID
            )
            guard status == noErr, pressedID.id == 1 else { return noErr }
            let manager = Unmanaged<HotKeyManager>.fromOpaque(userData).takeUnretainedValue()
            manager.callback?()
            return noErr
        }

        let installStatus = InstallEventHandler(
            GetApplicationEventTarget(),
            handler,
            1,
            [eventSpec],
            userData,
            &eventHandler
        )
        guard installStatus == noErr else {
            self.callback = nil
            throw HotkeyError.registrationFailed(value, installStatus)
        }

        let registerStatus = RegisterEventHotKey(
            parsed.keyCode,
            parsed.modifiers,
            hotKeyID,
            GetApplicationEventTarget(),
            0,
            &hotKey
        )
        guard registerStatus == noErr else {
            unregister()
            throw HotkeyError.registrationFailed(value, registerStatus)
        }
    }

    func unregister() {
        if let hotKey {
            UnregisterEventHotKey(hotKey)
            self.hotKey = nil
        }
        if let eventHandler {
            RemoveEventHandler(eventHandler)
            self.eventHandler = nil
        }
        callback = nil
    }

    deinit {
        unregister()
    }
}

// MARK: - Audio capture and WAV encoding

struct AudioRecording {
    let pcm: Data
    let sampleRate: Int
}

enum AudioRecorderError: LocalizedError {
    case permissionDenied
    case noInput
    case failed(String)

    var errorDescription: String? {
        switch self {
        case .permissionDenied:
            return "Microphone access is denied. Allow GPT Transcribe in System Settings → Privacy & Security → Microphone."
        case .noInput:
            return "No microphone input is available. Choose an input in System Settings → Sound."
        case .failed(let message):
            return "Could not start the microphone: \(message)"
        }
    }
}

final class AudioRecorder {
    private let lock = NSLock()
    private var engine: AVAudioEngine?
    private var pcmData = Data()
    private var sampleRate = 16_000
    private var recording = false

    func start(completion: @escaping (Result<Int, Error>) -> Void) {
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            guard let self else { return }
            let authorization = AVCaptureDevice.authorizationStatus(for: .audio)
            switch authorization {
            case .denied, .restricted:
                completion(.failure(AudioRecorderError.permissionDenied))
            case .notDetermined:
                AVCaptureDevice.requestAccess(for: .audio) { granted in
                    guard granted else {
                        completion(.failure(AudioRecorderError.permissionDenied))
                        return
                    }
                    self.configureAndStart(completion: completion)
                }
            case .authorized:
                self.configureAndStart(completion: completion)
            @unknown default:
                completion(.failure(AudioRecorderError.permissionDenied))
            }
        }
    }

    private func configureAndStart(completion: @escaping (Result<Int, Error>) -> Void) {
        do {
            let newEngine = AVAudioEngine()
            let input = newEngine.inputNode
            let format = input.inputFormat(forBus: 0)
            guard format.sampleRate > 0, format.channelCount > 0 else {
                completion(.failure(AudioRecorderError.noInput))
                return
            }

            lock.lock()
            pcmData.removeAll(keepingCapacity: true)
            sampleRate = Int(format.sampleRate.rounded())
            engine = newEngine
            recording = true
            lock.unlock()

            input.installTap(onBus: 0, bufferSize: 1024, format: format) { [weak self] buffer, _ in
                self?.append(buffer)
            }
            newEngine.prepare()
            try newEngine.start()
            completion(.success(Int(format.sampleRate.rounded())))
        } catch {
            lock.lock()
            engine = nil
            recording = false
            lock.unlock()
            completion(.failure(AudioRecorderError.failed(error.localizedDescription)))
        }
    }

    private func append(_ buffer: AVAudioPCMBuffer) {
        guard let channels = buffer.floatChannelData else { return }
        let frameCount = Int(buffer.frameLength)
        let channelCount = Int(buffer.format.channelCount)
        guard frameCount > 0, channelCount > 0 else { return }

        var samples = [Int16]()
        samples.reserveCapacity(frameCount)
        for frame in 0..<frameCount {
            var mixed: Float = 0
            for channel in 0..<channelCount {
                mixed += channels[channel][frame]
            }
            mixed /= Float(channelCount)
            let clamped = max(-1, min(1, mixed))
            samples.append(Int16(clamped * (clamped < 0 ? 32768 : 32767)))
        }

        lock.lock()
        defer { lock.unlock() }
        guard recording else { return }
        samples.withUnsafeBytes { pcmData.append(contentsOf: $0) }
    }

    func stop() -> AudioRecording {
        lock.lock()
        let currentEngine = engine
        let result = AudioRecording(pcm: pcmData, sampleRate: sampleRate)
        engine = nil
        pcmData = Data()
        recording = false
        lock.unlock()

        if let currentEngine {
            currentEngine.inputNode.removeTap(onBus: 0)
            currentEngine.stop()
        }
        return result
    }

    func cancel() {
        _ = stop()
    }
}

func makeWAV(pcm: Data, sampleRate: Int) -> Data {
    var output = Data()

    func appendASCII(_ value: String) {
        output.append(contentsOf: value.utf8)
    }

    func appendUInt32LE(_ value: UInt32) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { output.append(contentsOf: $0) }
    }

    func appendUInt16LE(_ value: UInt16) {
        var littleEndian = value.littleEndian
        withUnsafeBytes(of: &littleEndian) { output.append(contentsOf: $0) }
    }

    appendASCII("RIFF")
    appendUInt32LE(UInt32(36 + pcm.count))
    appendASCII("WAVE")
    appendASCII("fmt ")
    appendUInt32LE(16)
    appendUInt16LE(1)
    appendUInt16LE(1)
    appendUInt32LE(UInt32(sampleRate))
    appendUInt32LE(UInt32(sampleRate * 2))
    appendUInt16LE(2)
    appendUInt16LE(16)
    appendASCII("data")
    appendUInt32LE(UInt32(pcm.count))
    output.append(pcm)
    return output
}

// MARK: - Transcription client

enum TranscriptionError: LocalizedError {
    case missingAPIKey
    case request(String)
    case invalidResponse

    var errorDescription: String? {
        switch self {
        case .missingAPIKey:
            return "Add an OpenAI API key in Settings or set OPENAI_API_KEY before launching the app."
        case .request(let message):
            return message
        case .invalidResponse:
            return "OpenAI returned an unreadable transcription response."
        }
    }
}

func buildMultipart(
    fields: [String: String],
    filename: String,
    file: Data,
    mimeType: String
) -> (body: Data, boundary: String) {
    buildMultipart(fields: fields, repeatedFields: [:], filename: filename, file: file, mimeType: mimeType)
}

func buildMultipart(
    fields: [String: String],
    repeatedFields: [String: [String]],
    filename: String,
    file: Data,
    mimeType: String
) -> (body: Data, boundary: String) {
    let boundary = "----GPTTranscribe" + UUID().uuidString.replacingOccurrences(of: "-", with: "")
    var body = Data()

    func appendField(name: String, value: String) {
        body.append(contentsOf: "--\(boundary)\r\n".utf8)
        body.append(contentsOf: "Content-Disposition: form-data; name=\"\(name)\"\r\n\r\n".utf8)
        body.append(contentsOf: value.utf8)
        body.append(contentsOf: "\r\n".utf8)
    }
    for (name, value) in fields { appendField(name: name, value: value) }
    for (name, values) in repeatedFields {
        for value in values { appendField(name: name, value: value) }
    }
    body.append(contentsOf: "--\(boundary)\r\n".utf8)
    body.append(contentsOf: "Content-Disposition: form-data; name=\"file\"; filename=\"\(filename)\"\r\n".utf8)
    body.append(contentsOf: "Content-Type: \(mimeType)\r\n\r\n".utf8)
    body.append(file)
    body.append(contentsOf: "\r\n--\(boundary)--\r\n".utf8)
    return (body, boundary)
}

func apiErrorMessage(data: Data, status: Int) -> String {
    guard
        let object = try? JSONSerialization.jsonObject(with: data),
        let payload = object as? [String: Any]
    else {
        return "Transcription request failed with HTTP \(status)."
    }

    var message: String?
    if let error = payload["error"] as? [String: Any] {
        message = error["message"] as? String
    }
    if message == nil { message = payload["message"] as? String }
    guard let message else { return "Transcription request failed with HTTP \(status)." }
    let lowered = message.lowercased()
    if lowered.contains("api key") && ["incorrect", "invalid", "unauthorized", "rejected"].contains(where: lowered.contains) {
        return "OpenAI rejected the API key. Update it in Settings and retry."
    }
    return String(message.prefix(400))
}

final class TranscriptionClient {
    func transcribe(audio: Data, config: AppConfig, apiKey: String, completion: @escaping (Result<String, Error>) -> Void) {
        var fields = ["model": model, "response_format": "json"]
        if !config.prompt.isEmpty { fields["prompt"] = config.prompt }
        let multipart = buildMultipart(
            fields: fields,
            repeatedFields: [
                "keywords[]": config.keywords,
                "languages[]": config.languages,
            ],
            filename: "dictation.wav",
            file: audio,
            mimeType: "audio/wav"
        )

        var request = URLRequest(url: transcriptionURL)
        request.httpMethod = "POST"
        request.timeoutInterval = 180
        request.setValue("Bearer \(apiKey)", forHTTPHeaderField: "Authorization")
        request.setValue("multipart/form-data; boundary=\(multipart.boundary)", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")

        URLSession.shared.uploadTask(with: request, from: multipart.body) { data, response, error in
            if let error {
                completion(.failure(TranscriptionError.request("Could not reach OpenAI: \(error.localizedDescription)")))
                return
            }
            guard let httpResponse = response as? HTTPURLResponse, let data else {
                completion(.failure(TranscriptionError.invalidResponse))
                return
            }
            guard (200..<300).contains(httpResponse.statusCode) else {
                completion(.failure(TranscriptionError.request(apiErrorMessage(data: data, status: httpResponse.statusCode))))
                return
            }
            guard
                let object = try? JSONSerialization.jsonObject(with: data),
                let payload = object as? [String: Any],
                let text = payload["text"] as? String
            else {
                completion(.failure(TranscriptionError.invalidResponse))
                return
            }
            completion(.success(text.trimmingCharacters(in: .whitespacesAndNewlines)))
        }.resume()
    }
}

// MARK: - Launch at login and paste integration

private let accessibilitySettingsURL = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!

func hasAccessibilityPermission() -> Bool {
    // macOS versions expose this authorization through two related checks.
    // The Accessibility list shown in System Settings is reflected by AX,
    // while CGEvent preflight can be more specific on older releases.
    AXIsProcessTrusted() || CGPreflightPostEventAccess()
}

func requestAccessibilityPermission() {
    let options = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
    _ = AXIsProcessTrustedWithOptions(options)
    NSWorkspace.shared.open(accessibilitySettingsURL)
}

enum LaunchAtLogin {
    static func setEnabled(_ enabled: Bool) throws {
        if enabled {
            try SMAppService.mainApp.register()
        } else {
            try SMAppService.mainApp.unregister()
        }
    }
}

func pasteText(_ text: String, into application: NSRunningApplication?, completion: @escaping (Result<Void, Error>) -> Void) {
    let pasteboard = NSPasteboard.general
    let previous = pasteboard.string(forType: .string)
    pasteboard.clearContents()
    guard pasteboard.setString(text, forType: .string) else {
        completion(.failure(PasteError.couldNotWriteClipboard))
        return
    }

    if let application {
        application.activate(options: [.activateIgnoringOtherApps])
    }

    DispatchQueue.main.asyncAfter(deadline: .now() + 0.18) {
        guard hasAccessibilityPermission() else {
            completion(.failure(PasteError.accessibilityRequired))
            return
        }
        guard
            let source = CGEventSource(stateID: .combinedSessionState),
            let keyDown = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: true),
            let keyUp = CGEvent(keyboardEventSource: source, virtualKey: 9, keyDown: false)
        else {
            completion(.failure(PasteError.couldNotPostPaste))
            return
        }
        keyDown.flags = .maskCommand
        keyUp.flags = .maskCommand
        keyDown.post(tap: .cghidEventTap)
        keyUp.post(tap: .cghidEventTap)

        DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) {
            if NSPasteboard.general.string(forType: .string) == text, let previous {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(previous, forType: .string)
            }
        }
        completion(.success(()))
    }
}

enum PasteError: LocalizedError {
    case couldNotWriteClipboard
    case couldNotPostPaste
    case accessibilityRequired

    var errorDescription: String? {
        switch self {
        case .couldNotWriteClipboard:
            return "Could not write the macOS clipboard."
        case .couldNotPostPaste:
            return "Could not create the macOS paste event."
        case .accessibilityRequired:
            return "Allow GPT Transcribe in System Settings → Privacy & Security → Accessibility to paste into other apps."
        }
    }
}

// MARK: - Settings window

final class PasteableSecureTextField: NSSecureTextField {
    override func performKeyEquivalent(with event: NSEvent) -> Bool {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if modifiers == .command, event.charactersIgnoringModifiers?.lowercased() == "v" {
            pasteFromClipboard()
            return true
        }
        return super.performKeyEquivalent(with: event)
    }

    override func keyDown(with event: NSEvent) {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        if modifiers == .command, event.charactersIgnoringModifiers?.lowercased() == "v" {
            pasteFromClipboard()
            return
        }
        super.keyDown(with: event)
    }

    func pasteFromClipboard() {
        guard let value = NSPasteboard.general.string(forType: .string), !value.isEmpty else { return }
        stringValue = value.trimmingCharacters(in: .whitespacesAndNewlines)
        currentEditor()?.selectedRange = NSRange(location: stringValue.count, length: 0)
    }
}

final class SettingsWindowController: NSWindowController {
    private let hotkeyField: NSTextField
    private let languagesField: NSTextField
    private let promptField: NSTextField
    private let keywordsField: NSTextField
    private let maxSecondsField: NSTextField
    private let apiKeyField: PasteableSecureTextField
    private let launchAtLoginButton: NSButton
    private let apiKeyStatusLabel: NSTextField
    private let saveHandler: (AppConfig, String) -> Void
    private let removeKeyHandler: () -> Void

    init(config: AppConfig, keychainKeyExists: Bool, saveHandler: @escaping (AppConfig, String) -> Void, removeKeyHandler: @escaping () -> Void) {
        self.hotkeyField = NSTextField(string: config.hotkey)
        self.languagesField = NSTextField(string: config.languages.joined(separator: ", "))
        self.promptField = NSTextField(string: config.prompt)
        self.keywordsField = NSTextField(string: config.keywords.joined(separator: ", "))
        self.maxSecondsField = NSTextField(string: config.maxRecordingSeconds == 0 ? "" : String(config.maxRecordingSeconds))
        self.apiKeyField = PasteableSecureTextField(string: "")
        self.launchAtLoginButton = NSButton(checkboxWithTitle: "Launch GPT Transcribe at login", target: nil, action: nil)
        self.apiKeyStatusLabel = NSTextField(labelWithString: "")
        self.saveHandler = saveHandler
        self.removeKeyHandler = removeKeyHandler

        let window = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 520, height: 500),
            styleMask: [.titled, .closable],
            backing: .buffered,
            defer: false
        )
        window.title = "GPT Transcribe Settings"
        window.center()
        window.isReleasedWhenClosed = false
        super.init(window: window)

        hotkeyField.placeholderString = "ctrl+shift+space"
        languagesField.placeholderString = "Optional, e.g. en, fr"
        promptField.placeholderString = "Optional context about the recording"
        keywordsField.placeholderString = "Optional, comma-separated terms"
        maxSecondsField.alignment = .right
        maxSecondsField.placeholderString = "0 = unlimited, or 5–180"
        apiKeyField.placeholderString = "Stored securely in the macOS Keychain"
        launchAtLoginButton.state = config.launchAtLogin ? .on : .off
        apiKeyStatusLabel.textColor = .secondaryLabelColor
        apiKeyStatusLabel.font = .systemFont(ofSize: 11)
        apiKeyStatusLabel.stringValue = keychainKeyExists ? "A Keychain key is saved. Enter a new value to replace it." : "No Keychain key saved. OPENAI_API_KEY is also supported."

        let content = NSView()
        window.contentView = content
        let stack = NSStackView()
        stack.orientation = .vertical
        stack.alignment = .leading
        stack.spacing = 12
        stack.translatesAutoresizingMaskIntoConstraints = false
        content.addSubview(stack)
        NSLayoutConstraint.activate([
            stack.leadingAnchor.constraint(equalTo: content.leadingAnchor, constant: 24),
            stack.trailingAnchor.constraint(equalTo: content.trailingAnchor, constant: -24),
            stack.topAnchor.constraint(equalTo: content.topAnchor, constant: 22),
            stack.bottomAnchor.constraint(lessThanOrEqualTo: content.bottomAnchor, constant: -20),
        ])

        let heading = NSTextField(labelWithString: appName)
        heading.font = .boldSystemFont(ofSize: 18)
        stack.addArrangedSubview(heading)

        let subtitle = NSTextField(labelWithString: "Dictate into the text field that was focused when listening started.")
        subtitle.textColor = .secondaryLabelColor
        stack.addArrangedSubview(subtitle)

        let grid = NSGridView(views: [
            [NSTextField(labelWithString: "Hotkey"), hotkeyField],
            [NSTextField(labelWithString: "Languages"), languagesField],
            [NSTextField(labelWithString: "Prompt"), promptField],
            [NSTextField(labelWithString: "Keywords"), keywordsField],
            [NSTextField(labelWithString: "Max recording seconds"), maxSecondsField],
            [NSTextField(labelWithString: "Microphone"), NSTextField(labelWithString: "macOS default input")],
            [NSTextField(labelWithString: "OpenAI API key"), apiKeyInputRow()],
        ])
        grid.rowSpacing = 10
        grid.columnSpacing = 14
        grid.translatesAutoresizingMaskIntoConstraints = false
        grid.column(at: 0).width = 150
        grid.column(at: 1).width = 300
        stack.addArrangedSubview(grid)

        let keyRow = NSStackView(views: [apiKeyStatusLabel, NSView()])
        keyRow.orientation = .horizontal
        keyRow.alignment = .centerY
        keyRow.spacing = 8
        let removeButton = NSButton(title: "Remove saved key", target: self, action: #selector(removeSavedKey))
        removeButton.bezelStyle = .rounded
        keyRow.addArrangedSubview(removeButton)
        stack.addArrangedSubview(keyRow)

        stack.addArrangedSubview(launchAtLoginButton)

        let note = NSTextField(labelWithString: "Audio stays in memory until you stop listening, then is sent to OpenAI. Prompt, keywords, and language hints are optional. The API key is never written to the settings file.")
        note.textColor = .secondaryLabelColor
        note.font = .systemFont(ofSize: 11)
        note.maximumNumberOfLines = 2
        note.preferredMaxLayoutWidth = 465
        stack.addArrangedSubview(note)

        let accessibilityButton = NSButton(title: "Open Accessibility Settings", target: self, action: #selector(openAccessibilitySettings))
        accessibilityButton.bezelStyle = .rounded
        stack.addArrangedSubview(accessibilityButton)

        let spacer = NSView()
        spacer.setContentHuggingPriority(.defaultLow, for: .vertical)
        stack.addArrangedSubview(spacer)

        let buttons = NSStackView()
        buttons.orientation = .horizontal
        buttons.spacing = 8
        buttons.alignment = .centerY
        let cancel = NSButton(title: "Cancel", target: self, action: #selector(cancelClicked))
        let save = NSButton(title: "Save", target: self, action: #selector(saveClicked))
        save.keyEquivalent = "\r"
        buttons.addArrangedSubview(cancel)
        buttons.addArrangedSubview(save)
        buttons.translatesAutoresizingMaskIntoConstraints = false
        stack.addArrangedSubview(buttons)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) {
        fatalError("init(coder:) has not been implemented")
    }

    private func apiKeyInputRow() -> NSView {
        let pasteButton = NSButton(title: "Paste", target: self, action: #selector(pasteAPIKey))
        pasteButton.bezelStyle = .rounded
        let row = NSStackView(views: [apiKeyField, pasteButton])
        row.orientation = .horizontal
        row.alignment = .centerY
        row.spacing = 8
        apiKeyField.setContentHuggingPriority(.defaultLow, for: .horizontal)
        pasteButton.setContentHuggingPriority(.required, for: .horizontal)
        return row
    }

    @objc private func pasteAPIKey() {
        apiKeyField.pasteFromClipboard()
        apiKeyStatusLabel.stringValue = apiKeyField.stringValue.isEmpty ? "Clipboard does not contain text." : "Key pasted. Save to store it in Keychain."
    }

    @objc private func cancelClicked() {
        close()
    }

    @objc private func removeSavedKey() {
        removeKeyHandler()
        apiKeyStatusLabel.stringValue = "Saved Keychain key removed."
        apiKeyField.stringValue = ""
    }

    @objc private func openAccessibilitySettings() {
        NSWorkspace.shared.open(accessibilitySettingsURL)
    }

    @objc private func saveClicked() {
        var config = AppConfig(
            hotkey: hotkeyField.stringValue,
            prompt: promptField.stringValue,
            keywords: parseSettingList(keywordsField.stringValue),
            languages: parseSettingList(languagesField.stringValue),
            maxRecordingSeconds: parseMaxRecordingSeconds(maxSecondsField.stringValue),
            launchAtLogin: launchAtLoginButton.state == .on
        )
        do {
            _ = try parseHotkey(config.hotkey)
        } catch {
            showAlert(title: "Invalid hotkey", message: error.localizedDescription, parent: window)
            return
        }
        config.normalize()
        saveHandler(config, apiKeyField.stringValue.trimmingCharacters(in: .whitespacesAndNewlines))
    }
}

func showAlert(title: String, message: String, parent: NSWindow? = nil) {
    let alert = NSAlert()
    alert.alertStyle = .warning
    alert.messageText = title
    alert.informativeText = message
    if let parent {
        alert.beginSheetModal(for: parent)
    } else {
        alert.runModal()
    }
}

// MARK: - Application delegate

final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
    private enum State {
        case idle
        case starting
        case recording
        case transcribing
    }

    private let logger = AppLogger.shared
    private let configStore = ConfigStore()
    private let audioRecorder = AudioRecorder()
    private let transcriptionClient = TranscriptionClient()
    private let hotKeyManager = HotKeyManager()
    private var config = AppConfig()
    private var state: State = .idle
    private var status = "Ready"
    private var statusItem: NSStatusItem!
    private var menu: NSMenu!
    private var recordingTimer: Timer?
    private var targetApplication: NSRunningApplication?
    private var pendingRecordingURL: URL?
    private var settingsWindowController: SettingsWindowController?
    private var accessibilitySettingsOpened = false
    private let notificationCenter = UNUserNotificationCenter.current()
    private let statusNotificationIdentifier = "com.gpttranscribe.status"

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.accessory)
        config = configStore.load()
        let savedRecording = failedRecordingURL()
        if FileManager.default.fileExists(atPath: savedRecording.path) {
            pendingRecordingURL = savedRecording
        }
        buildStatusItem()
        registerHotkey()
        requestNotificationPermission()
        clearPreviousNotifications()
        logger.info("Started GPT Transcribe macOS \(appVersion)")
    }

    func applicationWillTerminate(_ notification: Notification) {
        recordingTimer?.invalidate()
        recordingTimer = nil
        audioRecorder.cancel()
        hotKeyManager.unregister()
        logger.info("Stopped")
    }

    private func buildStatusItem() {
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
        if let button = statusItem.button {
            button.image = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: appName)
            button.image?.isTemplate = true
            button.toolTip = appName
        }

        menu = NSMenu()
        menu.delegate = self
        menu.autoenablesItems = false
        statusItem.menu = menu
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        let recordItem = NSMenuItem(title: recordMenuTitle(), action: #selector(toggleRecording), keyEquivalent: "")
        recordItem.target = self
        menu.addItem(recordItem)
        let retryItem = NSMenuItem(title: "Retry failed recording", action: #selector(retryFailedRecording), keyEquivalent: "")
        retryItem.target = self
        retryItem.isEnabled = canRetry
        menu.addItem(retryItem)
        let deleteItem = NSMenuItem(title: "Delete saved recording", action: #selector(deleteSavedRecording), keyEquivalent: "")
        deleteItem.target = self
        deleteItem.isEnabled = canRetry
        menu.addItem(deleteItem)
        menu.addItem(.separator())

        let settingsItem = NSMenuItem(title: "Settings…", action: #selector(openSettings), keyEquivalent: ",")
        settingsItem.target = self
        menu.addItem(settingsItem)
        let logItem = NSMenuItem(title: "Open log folder", action: #selector(openLogFolder), keyEquivalent: "")
        logItem.target = self
        menu.addItem(logItem)
        menu.addItem(.separator())
        let quitItem = NSMenuItem(title: "Quit GPT Transcribe", action: #selector(quit), keyEquivalent: "q")
        quitItem.target = self
        menu.addItem(quitItem)
    }

    private func registerHotkey() {
        do {
            try hotKeyManager.register(config.hotkey) { [weak self] in
                DispatchQueue.main.async { self?.toggleRecording() }
            }
            setStatus("Ready")
        } catch {
            setStatus(error.localizedDescription, notify: true)
        }
    }

    private func recordMenuTitle() -> String {
        switch state {
        case .idle:
            return "Start listening (\(config.hotkey))"
        case .starting:
            return "Starting microphone…"
        case .recording:
            return "Stop listening (\(config.hotkey))"
        case .transcribing:
            return "Transcribing…"
        }
    }

    @objc private func toggleRecording() {
        switch state {
        case .idle: startRecording()
        case .recording: stopRecording()
        case .starting: break
        case .transcribing: notify(title: appName, message: "Please wait for the current dictation to finish.")
        }
    }

    private func requireAccessibilityPermission() -> Bool {
        guard hasAccessibilityPermission() else {
            setStatus(PasteError.accessibilityRequired.localizedDescription, notify: true)
            if !accessibilitySettingsOpened {
                accessibilitySettingsOpened = true
                requestAccessibilityPermission()
            }
            return false
        }
        return true
    }

    private func startRecording() {
        guard KeychainStore.configuredValue != nil else {
            setStatus("Add an API key in Settings", notify: true)
            openSettings()
            return
        }

        guard requireAccessibilityPermission() else { return }

        state = .starting
        targetApplication = currentTargetApplication()
        updateStatusItem()
        audioRecorder.start { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success:
                    guard self.state == .starting else { return }
                    self.state = .recording
                    self.status = "Listening… press the hotkey to finish"
                    self.updateStatusItem()
                    self.recordingTimer?.invalidate()
                    self.recordingTimer = nil
                    if self.config.maxRecordingSeconds > 0 {
                        self.recordingTimer = Timer.scheduledTimer(withTimeInterval: TimeInterval(self.config.maxRecordingSeconds), repeats: false) { [weak self] _ in
                            self?.stopRecording()
                        }
                    }
                case .failure(let error):
                    self.state = .idle
                    self.setStatus(error.localizedDescription, notify: true)
                }
            }
        }
    }

    @objc private func stopRecording() {
        guard state == .recording else { return }
        state = .transcribing
        recordingTimer?.invalidate()
        recordingTimer = nil
        let recording = audioRecorder.stop()
        let target = targetApplication
        targetApplication = nil
        status = "Transcribing…"
        updateStatusItem()

        let audio = makeWAV(pcm: recording.pcm, sampleRate: recording.sampleRate)
        guard audio.count >= 1_000 else {
            finish(status: "No audio captured", notify: true)
            return
        }
        transcribeAndPaste(audio: audio, target: target, pendingURL: nil)
    }

    private func transcribeAndPaste(audio: Data, target: NSRunningApplication?, pendingURL: URL?) {
        guard audio.count >= 1_000 else {
            finish(status: "No audio captured", notify: true)
            return
        }
        guard let apiKey = KeychainStore.configuredValue else {
            let saved = saveForRetry(audio)
            finish(status: failureStatus(TranscriptionError.missingAPIKey.localizedDescription, saved: saved), notify: true)
            return
        }
        let configSnapshot = config
        transcriptionClient.transcribe(audio: audio, config: configSnapshot, apiKey: apiKey) { [weak self] result in
            DispatchQueue.main.async {
                guard let self else { return }
                switch result {
                case .success(let transcript) where transcript.isEmpty:
                    let saved = self.saveForRetry(audio)
                    self.finish(status: self.failureStatus("No speech detected", saved: saved), notify: true)
                case .success(let transcript):
                    pasteText(transcript, into: target) { [weak self] pasteResult in
                        DispatchQueue.main.async { [weak self] in
                            guard let self else { return }
                            switch pasteResult {
                            case .success:
                                let removed: Bool
                                if let pendingURL {
                                    removed = self.removeSavedRecording(at: pendingURL)
                                } else {
                                    removed = true
                                }
                                self.finish(
                                    status: removed ? "Inserted transcript" : "Inserted transcript; saved recording retained",
                                    notify: !removed
                                )
                            case .failure(let error):
                                let saved = self.saveForRetry(audio)
                                self.finish(status: self.failureStatus(error.localizedDescription, saved: saved), notify: true)
                            }
                        }
                    }
                case .failure(let error):
                    self.logger.error("Transcription failed: \(error.localizedDescription)")
                    let saved = self.saveForRetry(audio)
                    self.finish(status: self.failureStatus(error.localizedDescription, saved: saved), notify: true)
                }
            }
        }
    }

    private func saveForRetry(_ audio: Data) -> Bool {
        do {
            pendingRecordingURL = try saveFailedRecording(audio)
            return true
        } catch {
            logger.error("Could not save failed recording: \(error.localizedDescription)")
            return false
        }
    }

    private func removeSavedRecording(at url: URL) -> Bool {
        do {
            try deleteFailedRecording(at: url)
            if pendingRecordingURL?.path == url.path {
                pendingRecordingURL = nil
            }
            return true
        } catch {
            logger.error("Could not delete saved recording: \(error.localizedDescription)")
            return false
        }
    }

    private func failureStatus(_ reason: String, saved: Bool) -> String {
        let message = reason.hasSuffix(".") ? reason : "\(reason)."
        return message + (saved ? " Saved recording for retry." : " Could not save recording for retry.")
    }

    private var canRetry: Bool {
        guard state == .idle, let url = pendingRecordingURL else { return false }
        return FileManager.default.fileExists(atPath: url.path)
    }

    private func currentTargetApplication() -> NSRunningApplication? {
        let application = NSWorkspace.shared.frontmostApplication
        return application?.bundleIdentifier == Bundle.main.bundleIdentifier ? nil : application
    }

    @objc private func retryFailedRecording() {
        guard state == .idle, let url = pendingRecordingURL else { return }
        guard requireAccessibilityPermission() else { return }
        guard FileManager.default.fileExists(atPath: url.path) else {
            pendingRecordingURL = nil
            finish(status: "Saved recording is unavailable", notify: true)
            return
        }

        let target = currentTargetApplication()
        state = .transcribing
        status = "Retrying saved recording…"
        updateStatusItem()

        DispatchQueue.global(qos: .utility).async { [weak self] in
            do {
                let audio = try Data(contentsOf: url)
                DispatchQueue.main.async {
                    self?.transcribeAndPaste(audio: audio, target: target, pendingURL: url)
                }
            } catch {
                DispatchQueue.main.async {
                    guard let self else { return }
                    self.logger.error("Could not read saved recording: \(error.localizedDescription)")
                    self.finish(status: "Could not read saved recording", notify: true)
                }
            }
        }
    }

    @objc private func deleteSavedRecording() {
        guard state == .idle, let url = pendingRecordingURL else { return }
        if removeSavedRecording(at: url) {
            finish(status: "Saved recording deleted", notify: true)
        } else {
            finish(status: "Could not delete saved recording", notify: true)
        }
    }

    private func finish(status: String, notify: Bool) {
        state = .idle
        setStatus(status, notify: notify)
    }

    private func setStatus(_ value: String, notify: Bool = false) {
        status = value
        updateStatusItem()
        logger.info(value)
        if notify { self.notify(title: appName, message: value) }
    }

    private func updateStatusItem() {
        guard let button = statusItem?.button else { return }
        switch state {
        case .recording:
            button.image = NSImage(systemSymbolName: "waveform", accessibilityDescription: "Listening")
        default:
            button.image = NSImage(systemSymbolName: "mic.fill", accessibilityDescription: appName)
        }
        button.image?.isTemplate = true
        button.toolTip = "\(appName) — \(status)"
        statusItem.menu?.update()
    }

    private func requestNotificationPermission() {
        notificationCenter.requestAuthorization(options: [.alert, .sound]) { _, error in
            if let error {
                self.logger.error("Notification permission request failed: \(error.localizedDescription)")
            }
        }
    }

    private func clearPreviousNotifications() {
        notificationCenter.getDeliveredNotifications { [weak self] notifications in
            guard let self else { return }
            let identifiers = notifications
                .filter { $0.request.content.title == appName }
                .map(\.request.identifier)
            guard !identifiers.isEmpty else { return }
            self.notificationCenter.removeDeliveredNotifications(withIdentifiers: identifiers)
        }
        notificationCenter.removePendingNotificationRequests(withIdentifiers: [statusNotificationIdentifier])
    }

    private func notify(title: String, message: String) {
        let content = UNMutableNotificationContent()
        content.title = title
        content.body = message
        let request = UNNotificationRequest(identifier: statusNotificationIdentifier, content: content, trigger: nil)
        notificationCenter.removePendingNotificationRequests(withIdentifiers: [statusNotificationIdentifier])
        notificationCenter.removeDeliveredNotifications(withIdentifiers: [statusNotificationIdentifier])
        notificationCenter.add(request) { [weak self] error in
            if let error {
                self?.logger.error("Could not deliver notification: \(error.localizedDescription)")
            }
        }
    }

    @objc private func openSettings() {
        if settingsWindowController == nil {
            settingsWindowController = SettingsWindowController(
                config: config,
                keychainKeyExists: KeychainStore.read() != nil,
                saveHandler: { [weak self] newConfig, apiKey in self?.saveSettings(newConfig, apiKey: apiKey) },
                removeKeyHandler: { try? KeychainStore.remove() }
            )
        }
        settingsWindowController?.showWindow(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func saveSettings(_ newConfig: AppConfig, apiKey: String) {
        let previousConfig = config
        do {
            if newConfig.launchAtLogin != config.launchAtLogin {
                try LaunchAtLogin.setEnabled(newConfig.launchAtLogin)
            }
            try hotKeyManager.register(newConfig.hotkey) { [weak self] in
                DispatchQueue.main.async { self?.toggleRecording() }
            }
            if !apiKey.isEmpty {
                try KeychainStore.save(apiKey)
            }
            config = newConfig
            configStore.save(config)
            settingsWindowController?.close()
            setStatus("Settings saved")
        } catch {
            config = previousConfig
            setStatus(error.localizedDescription, notify: true)
            showAlert(title: "Could not save settings", message: error.localizedDescription, parent: settingsWindowController?.window)
            registerHotkey()
        }
    }

    @objc private func openLogFolder() {
        NSWorkspace.shared.open(appSupportDirectory())
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}

final class ConfigStore {
    func load() -> AppConfig {
        AppConfig(defaults: .standard)
    }

    func save(_ config: AppConfig) {
        config.save(to: .standard)
    }
}

@main
struct GPTTranscribeMacMain {
    static func main() {
        let application = NSApplication.shared
        let delegate = AppDelegate()
        application.delegate = delegate
        application.run()
    }
}
