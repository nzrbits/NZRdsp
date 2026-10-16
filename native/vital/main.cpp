// SPDX-License-Identifier: GPL-3.0-or-later
#include "synth_base.h"
#include "sound_engine.h"
#include "synth_parameters.h"
#include "output.h"
#include "utils.h"
#include <algorithm>
#include <cmath>
#include <iostream>
#include <set>

using Json = nlohmann::json;
static constexpr auto pin = "636ca0ef517a4db087a6a08a6a8a5e704e21f836";

static juce::File absoluteFile(const Json& value) {
    const auto path = value.get<std::string>();
    require(path.find('\0') == std::string::npos, "Path contains a null character");
    const auto text = juce::String::fromUTF8(path.c_str());
    require(juce::File::isAbsolutePath(text), "Expected an absolute path");
    return juce::File(text);
}

class OfflineSynth : public HeadlessSynth {
public:
    using SynthBase::saveToJson;
    using SynthBase::processModulationChanges;
};

static Json readJson(const juce::File& file, int64_t limit) {
    require(file.existsAsFile() && file.getSize() > 0 && file.getSize() <= limit, "Invalid or oversized JSON file");
    auto value = Json::parse(file.loadFileAsString().toStdString());
    require(value.is_object(), "Expected a JSON object");
    return value;
}
static uint32_t integer(const Json& request, const char* key, uint32_t min, uint32_t max, uint32_t fallback) {
    if (!request.count(key)) return fallback;
    require(request[key].is_number_integer(), "Expected integer request field");
    const double value = request[key].get<double>();
    require(value >= min && value <= max, "Integer request field out of range");
    return uint32_t(value);
}
static float number(const Json& request, const char* key, float min, float max, float fallback) {
    if (!request.count(key)) return fallback;
    require(request[key].is_number(), "Expected numeric request field");
    const float value = request[key].get<float>();
    require(std::isfinite(value) && value >= min && value <= max, "Numeric request field out of range");
    return value;
}
static void validate(const vital::ValueDetails& details, float value) {
    require(std::isfinite(value) && value >= details.min && value <= details.max, "Parameter out of range");
    require(details.value_scale != vital::ValueDetails::kIndexed || std::floor(value) == value,
        "Indexed parameter requires an integer");
}

static Json run(const Json& request) {
    const std::set<std::string> fields = {"action", "preset", "output", "parameters", "note", "velocity",
        "duration_frames", "tail_frames", "sample_rate", "bpm", "seed"};
    for (auto it = request.begin(); it != request.end(); ++it)
        require(fields.count(it.key()) != 0, "Unknown request field");
    const auto action = request.at("action").get<std::string>();
    require(action == "parameters" || action == "save" || action == "render", "Unknown action");
    const auto seed = integer(request, "seed", 0, 1000000000, 0);
    vital::utils::RandomGenerator::next_seed_ = int(seed);
    OfflineSynth synth;
    synth.loadInitPreset();
    if (request.count("preset") && !request["preset"].is_null()) {
        const auto preset = absoluteFile(request["preset"]);
        auto state = readJson(preset, 64 * 1024 * 1024);
        require(state.count("synth_version") && state["synth_version"].is_string()
            && state.count("settings") && state["settings"].is_object(), "Invalid native preset");
        std::string error;
        const auto loaded = synth.loadFromFile(preset, error);
        require(loaded, error.empty() ? "Cannot load preset" : error.c_str());
    }
    if (request.count("parameters")) {
        require(request["parameters"].is_object(), "Parameters must be an object");
        for (auto it = request["parameters"].begin(); it != request["parameters"].end(); ++it) {
            require(synth.getControls().count(it.key()) != 0 && it.value().is_number(), "Unknown or nonnumeric parameter");
            const auto value = it.value().get<float>();
            validate(vital::Parameters::getDetails(it.key()), value);
            synth.valueChanged(it.key(), value);
        }
    }
    Json parameters = Json::object();
    for (const auto& entry : synth.getControls()) {
        const auto& details = vital::Parameters::getDetails(entry.first);
        validate(details, entry.second->value());
        parameters[entry.first] = {{"min", details.min}, {"max", details.max},
            {"default", details.default_value}, {"value", entry.second->value()},
            {"indexed", details.value_scale == vital::ValueDetails::kIndexed},
            {"scale", int(details.value_scale)}, {"display_name", details.display_name},
            {"units", details.display_units}};
    }
    Json result = {{"source_commit", pin}, {"engine_version", "1.0.6"}, {"seed", seed},
        {"action", action}, {"parameter_count", parameters.size()}};
    if (action == "parameters") {
        result["parameters"] = std::move(parameters);
        return result;
    }
    synth.checkOversampling();
    synth.processModulationChanges();
    const auto output = absoluteFile(request.at("output"));
    if (action == "save") {
        const auto text = synth.saveToJson().dump(2) + "\n";
        NewFile file(output);
        file.write(text.data(), text.size());
        file.finish();
        return result;
    }
    const auto rate = integer(request, "sample_rate", 8000, 192000, 48000);
    const auto note = integer(request, "note", 0, 127, 36);
    const auto velocity = integer(request, "velocity", 1, 127, 100);
    const auto duration = integer(request, "duration_frames", 1, rate * 600, rate * 2);
    const auto tail = integer(request, "tail_frames", 0, rate * 60, rate * 2);
    const auto bpm = number(request, "bpm", 5.0f, 900.0f, 120.0f);
    const auto frames = duration + tail;
    require(frames <= (UINT32_MAX - 48) / 8, "WAV exceeds RIFF limit");
    auto* engine = synth.getEngine();
    engine->setSampleRate(rate);
    synth.checkOversampling();
    engine->setBpm(bpm);
    engine->updateAllModulationSwitches();
    NewFile file(output);
    wavHeader(file, rate, frames);
    double peak = 0.0, energy = 0.0;
    uint64_t above_unity = 0;
    constexpr uint32_t quantum = 64;
    float audio[quantum * 2];
    for (uint32_t position = 0; position < frames;) {
        auto count = std::min(quantum, frames - position);
        if (position < duration) count = std::min(count, duration - position);
        engine->correctToTime(double(position) / rate);
        if (position == 0) engine->noteOn(note, float(velocity) / 127.0f, 0, 0);
        if (position == duration) engine->noteOff(note, 0.5f, 0, 0);
        engine->process(count);
        for (uint32_t frame = 0; frame < count; ++frame) {
            for (int channel = 0; channel < 2; ++channel) {
                const float value = engine->output(0)->buffer[frame][channel];
                require(std::isfinite(value), "Native engine returned nonfinite audio");
                audio[frame * 2 + channel] = value;
                peak = std::max(peak, double(std::abs(value)));
                energy += double(value) * value;
                above_unity += std::abs(value) > 1.0f;
            }
        }
        file.write(audio, sizeof(float) * count * 2);
        position += count;
    }
    file.finish();
    result.update({{"sample_rate", rate}, {"frames", frames}, {"duration_frames", duration},
        {"tail_frames", tail}, {"note", note}, {"velocity", velocity}, {"bpm", bpm},
        {"channels", 2}, {"format", "float32"}, {"maximum_block_size", quantum},
        {"peak", peak}, {"rms", std::sqrt(energy / (double(frames) * 2))},
        {"samples_above_unity", above_unity}});
    return result;
}

int wmain(int argc, wchar_t** argv) {
    try {
        require(argc == 2, "Internal renderer expects one JSON request file");
        const auto request = juce::File::getCurrentWorkingDirectory().getChildFile(juce::String(argv[1]));
        const auto result = run(readJson(request, 1024 * 1024));
        std::cout << result.dump() << std::endl;
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << std::endl;
        return 1;
    }
}
