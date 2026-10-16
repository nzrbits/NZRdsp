"""Create a test harness from a separately obtained, pinned Dexed source file."""
import argparse
import hashlib
from pathlib import Path

SOURCE_SHA256 = "b703cd36e052953556878e06332e1d835fb23660771f3ba08ee08ea7b85430a7"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    raw = args.source.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        parser.error("Source must match Cartridge.cpp at Dexed fea2f0f0f2c397799f59b27eb11c038cd34b22c5")
    source = raw.decode("utf-8")
    functions = []
    for signature in ["uint8_t normparm(", "Program Cartridge::unpackProgram("]:
        start = source.index(signature)
        end = source.index("\n}", start) + 2
        functions.append(source[start:end])
    harness = """#include <cstdint>
#include <cstring>
#include <fstream>
#include <iostream>
#include <string>
struct Program {
    static constexpr int PROGRAM_SIZE = 155;
    uint8_t data[155];
    Program(const uint8_t* source) { memcpy(data, source, 155); }
};
struct Cartridge {
    uint8_t voiceData[4104];
    Program unpackProgram(int);
};
""" + "\n".join(functions) + """
int main(int argc, char** argv) {
    if (argc != 3) return 1;
    const int index = std::stoi(argv[2]);
    if (index < 0 || index > 31) return 3;
    Cartridge cartridge;
    std::ifstream input(argv[1], std::ios::binary);
    if (!input.read(reinterpret_cast<char*>(cartridge.voiceData), 4104)) return 2;
    Program program = cartridge.unpackProgram(index);
    for (int i = 0; i < 155; i++) std::cout << int(program.data[i]) << " ";
}
"""
    with args.output.open("x", encoding="utf-8", newline="\n") as target:
        target.write(harness)
    print(args.output)


if __name__ == "__main__":
    main()
