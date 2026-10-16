// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "JuceHeader.h"
#include <cstdio>
#include <cstdint>
#include <fcntl.h>
#include <io.h>
#include <share.h>
#include <stdexcept>
#include <sys/stat.h>

inline void require(bool ok, const char* error) {
    if (!ok) throw std::runtime_error(error);
}

class NewFile {
public:
    explicit NewFile(const juce::File& path) : path_(path) {
        int descriptor = -1;
        require(_wsopen_s(&descriptor, path.getFullPathName().toWideCharPointer(),
            _O_WRONLY | _O_CREAT | _O_EXCL | _O_BINARY, _SH_DENYRW, _S_IREAD | _S_IWRITE) == 0,
            "Output must be a new writable file");
        file_ = _wfdopen(descriptor, L"wb");
        if (!file_) {
            _close(descriptor);
            path_.deleteFile();
            throw std::runtime_error("Open output stream");
        }
    }
    ~NewFile() {
        if (file_) std::fclose(file_);
        if (!finished_) path_.deleteFile();
    }
    NewFile(const NewFile&) = delete;
    NewFile& operator=(const NewFile&) = delete;
    void write(const void* data, size_t bytes) {
        require(std::fwrite(data, 1, bytes, file_) == bytes, "Write output");
    }
    void finish() {
        require(std::fflush(file_) == 0 && _commit(_fileno(file_)) == 0, "Flush output");
        const auto result = std::fclose(file_);
        file_ = nullptr;
        require(result == 0, "Close output");
        finished_ = true;
    }
private:
    juce::File path_;
    FILE* file_ = nullptr;
    bool finished_ = false;
};

inline void wavHeader(NewFile& file, uint32_t rate, uint32_t frames) {
    const auto tag = [&](const char* text) { file.write(text, 4); };
    const auto word = [&](uint16_t value) {
        const uint8_t bytes[] = {uint8_t(value), uint8_t(value >> 8)};
        file.write(bytes, 2);
    };
    const auto dword = [&](uint32_t value) {
        const uint8_t bytes[] = {uint8_t(value), uint8_t(value >> 8), uint8_t(value >> 16), uint8_t(value >> 24)};
        file.write(bytes, 4);
    };
    tag("RIFF"); dword(48 + frames * 8); tag("WAVE");
    tag("fmt "); dword(16); word(3); word(2);
    dword(rate); dword(rate * 8); word(8); word(32);
    tag("fact"); dword(4); dword(frames);
    tag("data"); dword(frames * 8);
}
