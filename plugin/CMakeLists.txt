cmake_minimum_required(VERSION 3.15)
project(BarChaser LANGUAGES CXX)

# Universal bundle for Resolume 7 on Intel and Apple Silicon Macs.
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_OSX_ARCHITECTURES "arm64;x86_64" CACHE STRING "" FORCE)
set(CMAKE_OSX_DEPLOYMENT_TARGET "10.15" CACHE STRING "" FORCE)
if(NOT CMAKE_BUILD_TYPE)
  set(CMAKE_BUILD_TYPE Release)
endif()

set(SDK ${CMAKE_CURRENT_SOURCE_DIR}/ffgl-sdk)
set(PUGI ${CMAKE_CURRENT_SOURCE_DIR}/third_party/pugixml)
file(GLOB SDK_SRC ${SDK}/ffgl/*.cpp ${SDK}/ffglex/*.cpp)

# ---- the effect ------------------------------------------------------------
add_library(BarChaser MODULE
  ${SDK_SRC}
  ${PUGI}/pugixml.cpp
  src/Preset.cpp
  src/BarChaser.cpp
)
target_include_directories(BarChaser PRIVATE ${SDK} ${PUGI} src)
target_compile_definitions(BarChaser PRIVATE TARGET_OS_MAC=1 NDEBUG=1)
target_compile_options(BarChaser PRIVATE -Wall -Wno-deprecated-declarations -Wno-unused-variable)
target_link_libraries(BarChaser PRIVATE "-framework OpenGL" "-framework Carbon" "-framework AppKit")
set_target_properties(BarChaser PROPERTIES
  OUTPUT_NAME "Bar Chaser"
  BUNDLE TRUE
  BUNDLE_EXTENSION "bundle"
  MACOSX_BUNDLE_INFO_PLIST ${CMAKE_CURRENT_SOURCE_DIR}/Info.plist.in
  MACOSX_BUNDLE_BUNDLE_NAME "Bar Chaser"
  MACOSX_BUNDLE_GUI_IDENTIFIER "com.mastanka.barchaser"
  MACOSX_BUNDLE_BUNDLE_VERSION "1.0"
  MACOSX_BUNDLE_SHORT_VERSION_STRING "1.0"
)

# ---- unit test for the preset parser (no OpenGL needed) --------------------
add_executable(test_preset tests/test_preset.cpp src/Preset.cpp ${PUGI}/pugixml.cpp)
target_include_directories(test_preset PRIVATE ${PUGI} src)
enable_testing()
add_test(NAME preset COMMAND test_preset ${CMAKE_CURRENT_SOURCE_DIR}/../tests/fixtures/preset_small.xml)
