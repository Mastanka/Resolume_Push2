#!/bin/sh
# Builds "Bar Chaser.bundle" (universal) into plugin/dist/ and runs the preset parser test.
#   brew install cmake        (once; Xcode command line tools are needed too)
#   plugin/build.sh
set -e
cd "$(dirname "$0")"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release > /dev/null
cmake --build build -j8
(cd build && ctest --output-on-failure)
rm -rf dist && mkdir -p dist
cp -R "build/Bar Chaser.bundle" dist/
echo "built: $(pwd)/dist/Bar Chaser.bundle"
file "dist/Bar Chaser.bundle/Contents/MacOS/Bar Chaser" | tail -2
