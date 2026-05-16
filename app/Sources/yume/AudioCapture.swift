import Foundation
import AVFoundation

/// Captures microphone audio with AVAudioEngine and converts to the Gradium
/// STT input format: 24 kHz, mono, 16-bit signed little-endian PCM.
final class AudioCapture {
    /// Called on the audio thread with raw PCM bytes ready to send.
    var onPCMFrame: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private let preprocessor = VoicePreprocessor()
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
        preprocessor.reset()
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
        preprocessor.process(channelData[0], count: frameCount)
        let byteCount = frameCount * 2 // int16 = 2 bytes
        let data = Data(bytes: channelData[0], count: byteCount)
        onPCMFrame?(data)
    }
}

private final class VoicePreprocessor {
    private var previousInput: Float = 0
    private var previousHighPass: Float = 0
    private var noiseFloorRMS: Float = 0.0025
    private var smoothedGain: Float = 1.0
    private var speechHangoverFrames = 0

    func reset() {
        previousInput = 0
        previousHighPass = 0
        noiseFloorRMS = 0.0025
        smoothedGain = 1.0
        speechHangoverFrames = 0
    }

    func process(_ samples: UnsafeMutablePointer<Int16>, count: Int) {
        guard count > 0 else { return }

        var processed = [Float](repeating: 0, count: count)
        var sumSquares: Float = 0

        for i in 0..<count {
            let input = Float(samples[i]) / 32768.0
            let highPassed = input - previousInput + 0.995 * previousHighPass
            previousInput = input
            previousHighPass = highPassed
            processed[i] = highPassed
            sumSquares += highPassed * highPassed
        }

        let rms = sqrt(sumSquares / Float(count))
        let speechThreshold = max(noiseFloorRMS * 1.8, 0.003)
        let isSpeech = rms > speechThreshold

        if isSpeech {
            speechHangoverFrames = 8
        } else {
            speechHangoverFrames = max(0, speechHangoverFrames - 1)
            let updateAlpha: Float = rms < noiseFloorRMS ? 0.90 : 0.98
            noiseFloorRMS = updateAlpha * noiseFloorRMS + (1 - updateAlpha) * rms
        }

        let speechActive = isSpeech || speechHangoverFrames > 0
        let attenuation: Float = speechActive ? 1.0 : 0.55
        let desiredGain: Float
        if speechActive {
            desiredGain = min(max(0.09 / max(rms, 0.001), 0.75), 3.2)
        } else {
            desiredGain = 1.0
        }
        smoothedGain = 0.92 * smoothedGain + 0.08 * desiredGain

        for i in 0..<count {
            let cleaned = softLimit(processed[i] * smoothedGain * attenuation)
            samples[i] = Int16(max(-32768, min(32767, Int(cleaned * 32767.0))))
        }
    }

    private func softLimit(_ sample: Float) -> Float {
        let clipped = max(-1.2, min(1.2, sample))
        return clipped / (1 + abs(clipped) * 0.18)
    }
}
