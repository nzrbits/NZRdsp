// SPDX-License-Identifier: GPL-3.0-or-later
#include "PluginProcessor.h"
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstdint>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <sys/stat.h>
#include <iostream>
#include <iomanip>
#include <stdexcept>
#include <vector>

static void require(bool condition, const char* error) {
    if (!condition) throw std::runtime_error(error);
}
static uint32_t integer(const wchar_t* text, uint32_t min, uint32_t max) {
    const auto input = juce::String(text).toStdString();
    require(!input.empty() && input.find_first_not_of("0123456789") == std::string::npos, "Expected unsigned integer");
    size_t end = 0;
    const auto value = std::stoull(input, &end);
    require(end == input.size() && value >= min && value <= max, "Integer out of range");
    return static_cast<uint32_t>(value);
}
static void le16(FILE* file, uint16_t value) {
    const uint8_t bytes[] = {uint8_t(value), uint8_t(value >> 8)};
    require(fwrite(bytes, 1, 2, file) == 2, "Write WAV header");
}
static void le32(FILE* file, uint32_t value) {
    const uint8_t bytes[] = {uint8_t(value), uint8_t(value >> 8), uint8_t(value >> 16), uint8_t(value >> 24)};
    require(fwrite(bytes, 1, 4, file) == 4, "Write WAV header");
}
static void tag(FILE* file, const char* value) {
    require(fwrite(value, 1, 4, file) == 4, "Write WAV tag");
}
static void header(FILE* file, uint32_t rate, uint32_t frames) {
    tag(file, "RIFF"); le32(file, 48 + frames * 8); tag(file, "WAVE");
    tag(file, "fmt "); le32(file, 16); le16(file, 3); le16(file, 2);
    le32(file, rate); le32(file, rate * 8); le16(file, 8); le16(file, 32);
    tag(file, "fact"); le32(file, 4); le32(file, frames);
    tag(file, "data"); le32(file, frames * 8);
}
static Program program(const juce::File& file) {
    juce::MemoryBlock data;
    require(file.getSize() == 163 && file.loadFileAsData(data), "Expected 163-byte single-voice SysEx");
    const auto* bytes = static_cast<const uint8_t*>(data.getData());
    require(bytes[0] == 0xf0 && bytes[1] == 0x43 && bytes[2] < 16 && bytes[3] == 0 && bytes[4] == 1 && bytes[5] == 27 && bytes[162] == 0xf7, "Invalid DX7 SysEx header");
    uint32_t sum = 0;
    for (int i = 6; i <= 161; ++i) { require(bytes[i] <= 127, "Invalid seven-bit data"); sum += bytes[i]; }
    require((sum & 127) == 0, "Invalid DX7 checksum");
    const int op_max[] = {99,99,99,99,99,99,99,99,99,99,99,3,3,7,3,7,99,1,31,99,14};
    for (int op = 0; op < 6; ++op)
        for (int i = 0; i < 21; ++i) require(bytes[6+op*21+i] <= op_max[i], "Invalid operator value");
    const int global_max[] = {99,99,99,99,99,99,99,99,31,7,1,99,99,99,99,1,5,7,48};
    for (int i = 0; i < 19; ++i) require(bytes[6+126+i] <= global_max[i], "Invalid global value");
    return Program(bytes + 6);
}

int wmain(int argc, wchar_t** argv) {
    if (argc != 11) {
        std::cerr << "Internal renderer: voice.syx output.wav note velocity duration_frames tail_frames rate block engine seed\n";
        return 2;
    }
    FILE* output = nullptr;
    bool created = false;
    const juce::File destination{juce::String(argv[2])};
    try {
        const auto voice = program(juce::File{juce::String(argv[1])});
        const auto note = integer(argv[3], 0, 127);
        const auto velocity = integer(argv[4], 1, 127);
        const auto duration = integer(argv[5], 1, 115200000);
        const auto tail = integer(argv[6], 0, 11520000);
        const auto rate = integer(argv[7], 22050, 192000);
        const auto block = integer(argv[8], 1, 8192);
        const auto engine = integer(argv[9], 0, 2);
        const auto seed = integer(argv[10], 0, UINT32_MAX);
        require(uint64_t(duration) + tail <= (UINT32_MAX - 48) / 8, "WAV exceeds RIFF limit");
        const auto frames = duration + tail;
        std::srand(seed);
        juce::ScopedJuceInitialiser_GUI framework;
        DexedAudioProcessor processor;
        processor.setNonRealtime(true);
        processor.setEngineType(engine);
        processor.setPlayConfigDetails(0, 2, rate, block);
        processor.applyProgram(voice);
        processor.prepareToPlay(rate, block);
        struct ReleaseResources {
            DexedAudioProcessor& processor;
            ~ReleaseResources() { processor.releaseResources(); }
        } release{processor};
        int descriptor = -1;
        const auto error = _wsopen_s(&descriptor, destination.getFullPathName().toWideCharPointer(), _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY, _SH_DENYRW, _S_IREAD | _S_IWRITE);
        require(error == 0, "Output must be a new writable file");
        created = true;
        output = _wfdopen(descriptor, L"wb");
        if (!output) { _close(descriptor); throw std::runtime_error("Open output stream"); }
        header(output, rate, frames);
        juce::AudioBuffer<float> audio(2, block);
        std::vector<float> interleaved(block * 2);
        double peak = 0.0, energy = 0.0;
        for (uint32_t position = 0; position < frames;) {
            const auto count = std::min(block, frames - position);
            audio.setSize(2, int(count), false, false, true);
            audio.clear();
            juce::MidiBuffer midi;
            if (position == 0) midi.addEvent(juce::MidiMessage::noteOn(1, int(note), uint8_t(velocity)), 0);
            if (duration >= position && duration < position + count)
                midi.addEvent(juce::MidiMessage::noteOff(1, int(note)), int(duration - position));
            processor.processBlock(audio, midi);
            for (uint32_t frame = 0; frame < count; ++frame) {
                for (int channel = 0; channel < 2; ++channel) {
                    const auto sample = audio.getSample(channel, int(frame));
                    require(std::isfinite(sample), "Native engine returned non-finite audio");
                    interleaved[frame*2+channel] = sample;
                    peak = std::max(peak, double(std::abs(sample)));
                    energy += double(sample) * sample;
                }
            }
            require(fwrite(interleaved.data(), sizeof(float)*2, count, output) == count, "Write WAV audio");
            position += count;
        }
        const auto close_result = fclose(output);
        output = nullptr;
        require(close_result == 0, "Close WAV output");
        std::cout << std::setprecision(17) << "{\"frames\":" << frames << ",\"sample_rate\":" << rate
                  << ",\"channels\":2,\"format\":\"float32\",\"source_commit\":\"fea2f0f0f2c397799f59b27eb11c038cd34b22c5\",\"engine\":" << engine
                  << ",\"note\":" << note << ",\"velocity\":" << velocity
                  << ",\"duration_frames\":" << duration << ",\"tail_frames\":" << tail
                  << ",\"block\":" << block << ",\"native_quantum\":64"
                  << ",\"seed\":" << seed << ",\"peak\":" << peak
                  << ",\"rms\":" << std::sqrt(energy/(double(frames)*2)) << "}\n";
        return 0;
    } catch (const std::exception& error) {
        if (output) fclose(output);
        if (created) destination.deleteFile();
        std::cerr << error.what() << "\n";
        return 1;
    }
}
