import Foundation
import XCTest
@testable import GPTTranscribeMac

final class GPTTranscribeMacTests: XCTestCase {
    func testParsesDefaultHotkey() throws {
        let parsed = try parseHotkey("ctrl+shift+space")
        XCTAssertEqual(parsed.keyCode, 49)
        XCTAssertNotEqual(parsed.modifiers, 0)
    }

    func testRejectsModifierOnlyHotkey() {
        XCTAssertThrowsError(try parseHotkey("ctrl"))
    }

    func testWAVHeaderContainsExpectedFormat() throws {
        let pcm = Data(repeating: 0, count: 1_600)
        let wav = makeWAV(pcm: pcm, sampleRate: 16_000)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(String(data: wav.subdata(in: 8..<12), encoding: .ascii), "WAVE")
        XCTAssertEqual(wav.count, pcm.count + 44)
    }

    func testConfigNormalizesRecordingLimit() {
        let config = AppConfig(hotkey: "", language: " en ", maxRecordingSeconds: 999)
        XCTAssertEqual(config.hotkey, "ctrl+shift+space")
        XCTAssertEqual(config.language, "en")
        XCTAssertEqual(config.maxRecordingSeconds, 180)
    }

    func testMultipartContainsModelAndAudio() {
        let multipart = buildMultipart(fields: ["model": "gpt-transcribe"], filename: "dictation.wav", file: Data("abc".utf8), mimeType: "audio/wav")
        XCTAssertTrue(multipart.body.contains(Data("gpt-transcribe".utf8)))
        XCTAssertTrue(multipart.body.contains(Data("dictation.wav".utf8)))
        XCTAssertTrue(multipart.body.contains(Data("abc".utf8)))
        XCTAssertTrue(multipart.body.contains(Data(multipart.boundary.utf8)))
    }

    func testFailedRecordingIsSavedAsTheLatestWAV() throws {
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent("GPTTranscribeTests-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: directory) }

        let path = try saveFailedRecording(Data("first".utf8), in: directory)
        XCTAssertEqual(path, failedRecordingURL(in: directory))
        XCTAssertEqual(try Data(contentsOf: path), Data("first".utf8))

        _ = try saveFailedRecording(Data("latest".utf8), in: directory)
        XCTAssertEqual(try Data(contentsOf: path), Data("latest".utf8))

        try deleteFailedRecording(at: path)
        XCTAssertFalse(FileManager.default.fileExists(atPath: path.path))
    }
}
