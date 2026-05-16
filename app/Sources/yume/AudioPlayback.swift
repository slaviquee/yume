import Foundation
import AVFoundation

/// Streams 48 kHz mono signed-16 PCM (Gradium TTS format) to the default
/// output device via AVAudioEngine + AVAudioPlayerNode.
final class AudioPlayback {
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(
        commonFormat: .pcmFormatInt16,
        sampleRate: 48000,
        channels: 1,
        interleaved: true
    )!
    private var prepared = false

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
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { _ in }
    }

    func stop() {
        guard prepared else { return }
        player.stop()
        engine.stop()
        prepared = false
    }
}
