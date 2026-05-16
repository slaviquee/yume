import Foundation
import AVFoundation

/// Captures microphone audio with AVAudioEngine and converts to the Gradium
/// STT preferred input format: 24 kHz, mono, 16-bit signed little-endian PCM.
final class AudioCapture {
    /// Called on the audio thread with raw PCM bytes ready to send.
    var onPCMFrame: ((Data) -> Void)?

    private let engine = AVAudioEngine()
    private var converter: AVAudioConverter?
    private var monoInputFormat: AVAudioFormat?
    private let preprocessor = VoicePreprocessor()
    private var systemVoiceProcessingEnabled = false
    private var inputFormatDescription = ""
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
        configureSystemVoiceProcessing(on: input)
        let inputFormat = input.outputFormat(forBus: 0)
        inputFormatDescription = inputFormat.description
        guard let monoFormat = AVAudioFormat(
            commonFormat: .pcmFormatFloat32,
            sampleRate: inputFormat.sampleRate,
            channels: 1,
            interleaved: false
        ) else {
            NSLog("yume: failed to create mono mic format from %@", inputFormat)
            return
        }
        monoInputFormat = monoFormat
        converter = AVAudioConverter(from: monoFormat, to: outputFormat)
        preprocessor.reset()
        guard converter != nil else {
            NSLog("yume: failed to create audio converter from %@ via %@", inputFormat, monoFormat)
            return
        }
        NSLog(
            "yume: mic capture format=%@ monoInput=%@ output=24k-int16 systemVoiceProcessing=%@ localCleanup=%@",
            inputFormatDescription,
            monoFormat.description,
            systemVoiceProcessingEnabled ? "on" : "off",
            shouldUseLocalPreprocessor ? "on" : "off"
        )
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
        guard let converter, let monoInput = makeMonoInputBuffer(from: input) else { return }
        // Output buffer sized for 80 ms at 24 kHz = 1920 frames.
        let targetFrames = AVAudioFrameCount(outputFormat.sampleRate * 0.08)
        guard let outBuffer = AVAudioPCMBuffer(pcmFormat: outputFormat, frameCapacity: targetFrames) else { return }

        var error: NSError?
        var providedInput = false
        let status = converter.convert(to: outBuffer, error: &error) { _, outStatus in
            if providedInput {
                outStatus.pointee = .noDataNow
                return nil
            }
            providedInput = true
            outStatus.pointee = .haveData
            return monoInput
        }

        if status == .error || error != nil {
            return
        }

        guard let channelData = outBuffer.int16ChannelData else { return }
        let frameCount = Int(outBuffer.frameLength)
        guard frameCount > 0 else { return }
        if shouldUseLocalPreprocessor {
            preprocessor.process(channelData[0], count: frameCount)
        }
        let byteCount = frameCount * 2 // int16 = 2 bytes
        let data = Data(bytes: channelData[0], count: byteCount)
        onPCMFrame?(data)
    }

    private func makeMonoInputBuffer(from input: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        guard let monoInputFormat else { return nil }
        let frameCount = Int(input.frameLength)
        guard frameCount > 0 else { return nil }
        guard input.format.commonFormat == .pcmFormatFloat32,
              let inputData = input.floatChannelData,
              let monoBuffer = AVAudioPCMBuffer(
                pcmFormat: monoInputFormat,
                frameCapacity: AVAudioFrameCount(frameCount)
              ),
              let monoData = monoBuffer.floatChannelData?[0] else {
            return nil
        }

        monoBuffer.frameLength = AVAudioFrameCount(frameCount)
        let channelCount = Int(input.format.channelCount)
        guard channelCount > 0 else { return nil }

        let selectedChannel = loudestChannel(
            in: inputData,
            channelCount: channelCount,
            frameCount: frameCount,
            interleaved: input.format.isInterleaved
        )

        if input.format.isInterleaved {
            let source = inputData[0]
            for i in 0..<frameCount {
                monoData[i] = source[i * channelCount + selectedChannel]
            }
        } else {
            let source = inputData[selectedChannel]
            monoData.update(from: source, count: frameCount)
        }

        return monoBuffer
    }

    private func loudestChannel(
        in data: UnsafePointer<UnsafeMutablePointer<Float>>,
        channelCount: Int,
        frameCount: Int,
        interleaved: Bool
    ) -> Int {
        var bestChannel = 0
        var bestEnergy: Float = -1

        for channel in 0..<channelCount {
            var energy: Float = 0
            if interleaved {
                let source = data[0]
                for i in 0..<frameCount {
                    let sample = source[i * channelCount + channel]
                    energy += sample * sample
                }
            } else {
                let source = data[channel]
                for i in 0..<frameCount {
                    let sample = source[i]
                    energy += sample * sample
                }
            }
            if energy > bestEnergy {
                bestEnergy = energy
                bestChannel = channel
            }
        }

        return bestChannel
    }

    private var shouldUseLocalPreprocessor: Bool {
        ProcessInfo.processInfo.environment["YUME_DISABLE_LOCAL_AUDIO_CLEANUP"] != "1"
    }

    private func configureSystemVoiceProcessing(on input: AVAudioInputNode) {
        guard ProcessInfo.processInfo.environment["YUME_ENABLE_SYSTEM_VOICE_PROCESSING"] == "1" else {
            systemVoiceProcessingEnabled = false
            return
        }
        do {
            try input.setVoiceProcessingEnabled(true)
            systemVoiceProcessingEnabled = true
        } catch {
            systemVoiceProcessingEnabled = false
            NSLog("yume: system voice processing unavailable: %@", error.localizedDescription)
        }
    }
}

private final class VoicePreprocessor {
    private var previousInput: Float = 0
    private var previousHighPass: Float = 0
    private var noiseFloorRMS: Float = 0.00035
    private var smoothedGain: Float = 1.0
    private var speechHangoverFrames = 0

    func reset() {
        previousInput = 0
        previousHighPass = 0
        noiseFloorRMS = 0.00035
        smoothedGain = 1.0
        speechHangoverFrames = 0
    }

    func process(_ samples: UnsafeMutablePointer<Int16>, count: Int) {
        guard count > 0 else { return }

        var processed = [Float](repeating: 0, count: count)
        var sumSquares: Float = 0
        var peak: Float = 0

        for i in 0..<count {
            let input = Float(samples[i]) / 32768.0
            let highPassed = input - previousInput + 0.995 * previousHighPass
            previousInput = input
            previousHighPass = highPassed
            processed[i] = highPassed
            sumSquares += highPassed * highPassed
            peak = max(peak, abs(highPassed))
        }

        let rms = sqrt(sumSquares / Float(count))
        let speechThreshold = max(noiseFloorRMS * 2.0, 0.00045)
        let peakThreshold = max(noiseFloorRMS * 7.0, 0.0020)
        let isSpeech = rms > speechThreshold || peak > peakThreshold

        if isSpeech {
            speechHangoverFrames = 12
        } else {
            speechHangoverFrames = max(0, speechHangoverFrames - 1)
            let updateAlpha: Float = rms < noiseFloorRMS ? 0.92 : 0.995
            noiseFloorRMS = updateAlpha * noiseFloorRMS + (1 - updateAlpha) * rms
        }

        let speechActive = isSpeech || speechHangoverFrames > 0
        let attenuation: Float = speechActive ? 1.0 : 0.35
        let desiredGain: Float
        if speechActive {
            let rmsGain = 0.08 / max(rms, 0.00025)
            let peakGain = 0.88 / max(peak, 0.0001)
            desiredGain = min(max(rmsGain, 1.0), peakGain, 36.0)
        } else {
            desiredGain = 1.0
        }
        let gainAlpha: Float = desiredGain > smoothedGain ? 0.35 : 0.05
        smoothedGain = (1 - gainAlpha) * smoothedGain + gainAlpha * desiredGain

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
