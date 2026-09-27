// Advanced Output preset (Arena → Output → Advanced → Presets → Save) → bar rectangles.
#pragma once
#include <string>
#include <vector>

namespace barchaser
{
struct Slice
{
	std::string name;              // fixture "Lumiverse 3 / 1 - 855 h3 2m grb" or whole screen "Lumiverse 3"
	float left, top, right, bottom;// composition pixels, y from the top
};

struct Preset
{
	float width  = 1920.f;         // CurrentCompositionTextureSize of the preset
	float height = 1080.f;
	std::vector< Slice > entries;  // every fixture ("Screen / slice") in Arena's list order, then every whole screen
	size_t fixtures = 0;           // entries[0 .. fixtures-1] are the fixtures
};

// ~/Documents/Resolume Arena/Presets/Advanced Output
std::string defaultPresetFolder();
// Path of the most recently changed .xml in folder, or "" when there is none.
std::string newestPreset( const std::string& folder );
// Resolve what the user typed in the Preset parameter: "" = newest, a name = <folder>/<name>.xml,
// a path ending in .xml = that file.
std::string resolvePreset( const std::string& text, const std::string& folder );
bool loadPreset( const std::string& path, Preset& out, std::string& error );
}// namespace barchaser
