import Foundation
import AVFoundation

/// Captures microphone audio with AVAudioEngine and converts to the Gradium
/// STT input format: 24 kHz, mono, 16-bit signed little-endian PCM.
final class AudioCapture {
    /// Called on the audio thread with raw PCM bytes ready to send.
    var onPCMFrame: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private let outputFormat = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 24000,
        channels: 1,
        interleaved: true
    )!

    private var running = false

    func start() {
        guard !running else { return }
        let input = engine.inputNode
        let inputFormat = input.outputFormat(forBus: 0)
        converter = AVAudioConverter(from: inputFormat, to: outputFormat)
        guard converter != nil else {
            NSLog("yume: failed to create audio converter from %@", inputFormat)
            return
        }
        // 80ms buffers at the input's native rate.
        let buffer = AVAudioFrameCount(inputFormat.sampleRate * 0.08)
        input.installTap(onBus: 0, bufferSize: buffer, format: inputFormat) { [weak self] inputBuffer, _ in
            self?.convertAndEmit(inputBuffer)
        }
        do {
            engine.prepare()
            try engine.start()
            running = true
        } catch {
            NSLog("yume: audio engine start failed: %@", error.localizedDescription)
        }
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        running = false
    }

    private func convertAndEmit(_ input: AVAudioPCMBuffer) {
        guard let converter else { return }
        // Output buffer sized for 80 ms at 24 kHz = 1920 frames.
        let targetFrames = AVAudioFrameCount(outputFormat.sampleRate * 0.08)
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: targetFrames) else { return }

        var error: NSError?
        var providedInput = false
        let status = converter.convert(to: outBuffer, error: &error) { _, outStatus in
            if providedInput {
                outStatus.pointee = .endOfStream
                return nil
            }
            providedInput = true
            outStatus.pointee = .haveData
            return input
        }

        if status == .error || error != nil {
            return
        }

        guard let channelData = outBuffer.int16ChannelData else { return }
        let frameCount = Int(outBuffer.frameLength)
        let byteCount = frameCount * 2 // int16 = 2 bytes
        let data = Data(bytes: channelData[0], count: byteCount)
        onPCMFrame?(data)
    }
}
