import Foundation
import AVFoundation

/// Streams 48 kHz mono signed-16 PCM (Gradium TTS format) to the default
/// output device via AVAudioEngine + AVAudioPlayerNode.
final class AudioPlayback {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    var onDrained: (() -> Void)?

    private let format = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 48000,
        channels: 1,
        interleaved: true
    )!
    private var prepared = false
    private let stateQueue = DispatchQueue(label: "yume.audioPlayback.state")
    private var pendingBuffers = 0

    var isIdle: Bool {
        stateQueue.sync { pendingBuffers == 0 }
    }

    func prepare() {
        guard !prepared else { return }
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
        do {
            engine.prepare()
            try engine.start()
            player.play()
            prepared = true
        } catch {
            NSLog("yume: playback engine start failed: %@", error.localizedDescription)
        }
    }

    func enqueue(pcm: Data) {
        if !prepared { prepare() }
        let frameCount = AVAudioFrameCount(pcm.count / 2)
        guard frameCount > 0,
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount) else { return }
        buffer.frameLength = frameCount
        pcm.withUnsafeBytes { raw in
            if let src = raw.baseAddress, let dst = buffer.int16ChannelData?[0] {
                memcpy(dst, src, pcm.count)
            }
        }
        stateQueue.sync {
            pendingBuffers += 1
        }
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            guard let self else { return }
            var drained = false
            self.stateQueue.sync {
                self.pendingBuffers = max(0, self.pendingBuffers - 1)
                drained = self.pendingBuffers == 0
            }
            if drained {
                DispatchQueue.main.async { [weak self] in
                    self?.onDrained?()
                }
            }
        }
    }

    func stop() {
        guard prepared else { return }
        player.stop()
        engine.stop()
        stateQueue.sync {
            pendingBuffers = 0
        }
        prepared = false
        DispatchQueue.main.async { [weak self] in
            self?.onDrained?()
        }
    }
}
