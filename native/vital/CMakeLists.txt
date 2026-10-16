cmake_minimum_required(VERSION 3.24)
project(NZRdspSpectralRenderer LANGUAGES C CXX)
if(NOT WIN32 OR NOT MSVC OR NOT CMAKE_SIZEOF_VOID_P EQUAL 8)
  message(FATAL_ERROR "This target currently requires Windows x64 MSVC")
endif()
set(VITAL_SOURCE_ROOT "" CACHE PATH "Pinned external source checkout")
find_package(Git REQUIRED)
execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${VITAL_SOURCE_ROOT}" rev-parse HEAD
  OUTPUT_VARIABLE revision OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
if(NOT revision STREQUAL "636ca0ef517a4db087a6a08a6a8a5e704e21f836")
  message(FATAL_ERROR "Unexpected source revision")
endif()
execute_process(COMMAND "${GIT_EXECUTABLE}" -C "${VITAL_SOURCE_ROOT}" status --porcelain --untracked-files=no
  OUTPUT_VARIABLE dirty OUTPUT_STRIP_TRAILING_WHITESPACE COMMAND_ERROR_IS_FATAL ANY)
if(NOT dirty STREQUAL "")
  message(FATAL_ERROR "Preserve tracked source changes before building")
endif()

# Isolate user configuration in a build copy; synthesis and preset code stay upstream.
file(READ "${VITAL_SOURCE_ROOT}/src/common/synth_base.cpp" synth_base)
set(startup_call "Startup::doStartupChecks(midi_manager_.get());")
string(FIND "${synth_base}" "${startup_call}" startup_position)
if(startup_position EQUAL -1)
  message(FATAL_ERROR "Expected startup hook is missing")
endif()
string(REPLACE "${startup_call}" "/* NZRdsp offline: no personal startup configuration */" synth_base "${synth_base}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/synth_base-offline.cpp" "${synth_base}")
file(READ "${VITAL_SOURCE_ROOT}/src/unity_build/common.cpp" common)
string(REPLACE "\"synth_base.cpp\"" "\"${CMAKE_CURRENT_BINARY_DIR}/synth_base-offline.cpp\"" common "${common}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/common-offline.cpp" "${common}")
file(READ "${VITAL_SOURCE_ROOT}/headless/JuceLibraryCode/JuceHeader.h" juce_header)
string(REPLACE "\"99999.9.9\"" "\"1.0.6\"" juce_header "${juce_header}")
string(REPLACE "0x869f0909" "0x10006" juce_header "${juce_header}")
string(REPLACE "\"Vital\"" "\"NZRdsp spectral renderer\"" juce_header "${juce_header}")
file(WRITE "${CMAKE_CURRENT_BINARY_DIR}/JuceHeader.h" "${juce_header}")

add_executable(nzrdsp-spectral-native main.cpp
  "${CMAKE_CURRENT_BINARY_DIR}/common-offline.cpp"
  "${VITAL_SOURCE_ROOT}/src/unity_build/synthesis.cpp")
target_compile_features(nzrdsp-spectral-native PRIVATE cxx_std_17)
set_property(TARGET nzrdsp-spectral-native PROPERTY MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
target_compile_options(nzrdsp-spectral-native PRIVATE /bigobj /utf-8 /Zc:__cplusplus)
target_compile_definitions(nzrdsp-spectral-native PRIVATE HEADLESS=1 NO_AUTH=1
  JUCE_USE_CURL=0 JUCE_WEB_BROWSER=0 JUCE_USE_WINDOWS_MEDIA_FORMAT=0
  __SSE2__=1 _ENABLE_EXTENDED_ALIGNED_STORAGE
  _USE_MATH_DEFINES _CRT_SECURE_NO_WARNINGS NOMINMAX)
target_include_directories(nzrdsp-spectral-native PRIVATE
  "${CMAKE_CURRENT_BINARY_DIR}"
  "${VITAL_SOURCE_ROOT}/headless/JuceLibraryCode"
  "${VITAL_SOURCE_ROOT}/third_party/JUCE/modules"
  "${VITAL_SOURCE_ROOT}/third_party"
  "${VITAL_SOURCE_ROOT}/src/common"
  "${VITAL_SOURCE_ROOT}/src/common/wavetable")
target_include_directories(nzrdsp-spectral-native PRIVATE "${VITAL_SOURCE_ROOT}/src/interface/look_and_feel")
foreach(directory IN ITEMS synth_engine effects filters framework lookups modulators modules producers utilities)
  target_include_directories(nzrdsp-spectral-native PRIVATE "${VITAL_SOURCE_ROOT}/src/synthesis/${directory}")
endforeach()
foreach(module IN ITEMS audio_basics audio_formats core data_structures dsp events)
  target_sources(nzrdsp-spectral-native PRIVATE "${VITAL_SOURCE_ROOT}/headless/JuceLibraryCode/include_juce_${module}.cpp")
endforeach()
