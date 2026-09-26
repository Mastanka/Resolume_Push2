// Advanced Output preset (Arena → Output → Advanced → Presets → Save) → bar rectangles.
#pragma once
#include <string>
#include <vector>

namespace barchaser
{
struct Slice
{
	std::string name;              // "Lumiverse 3" or "Lumiverse 3 / 1 - 855 h3 2m grb"
	float left, top, right, bottom;// composition pixels, y from the top
};

struct Preset
{
	float width  = 1920.f;         // CurrentCompositionTextureSize of the preset
	float height = 1080.f;
	std::vector< Slice > entries;  // screens sorted left→right, then "Screen / slice" for multi-slice screens
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
